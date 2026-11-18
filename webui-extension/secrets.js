/* Tabbie — Settings → Accounts → Secrets (plain JS, no build step). Pairs with secrets.css.
 * API keys Tabbie's workers can use, kept in a 1Password vault (sidecar secrets.py). This page only ever sees
 * names: each secret's name, its variable (RESEND_API_KEY) and provider. Values are shown as dots, always.
 *   Add / Replace: the value goes from this form straight to 1Password; the field is cleared at once.
 *   Rotate (Resend, Cloudflare): the provider makes a new key, it's saved to 1Password, and the new value is
 *     shown here ONCE with a Copy button. It lives only in this page's memory: switch section, leave or
 *     refresh and it's gone for good.
 *   Not connected yet: the steps to connect 1Password (service account token -> macOS Keychain).
 * No confirm()/alert() dialogs: deletes and rotations confirm inline.
 */
(() => {
  'use strict';
  if (window.__hxSecrets) return;
  window.__hxSecrets = true;

  const API = 'api/extensions/cc-agents/sidecar/';
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const icon = (n, s) => (typeof window.hxIcon === 'function' ? window.hxIcon(n, s || 14) : '');
  let box = null, state = null, loading = false, mode = null, err = '', revealed = null, busyId = '';

  async function get(force) {
    const r = await fetch(API + 'secrets' + (force ? '?force=1' : ''), { credentials: 'same-origin', cache: 'no-store' });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) { const e = new Error(j.error || String(r.status)); e.status = r.status; throw e; }
    return j;
  }
  async function post(path, body) {
    const r = await fetch(API + 'secrets/' + path, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok || j.ok === false) throw new Error(j.error || 'Something went wrong.');
    return j;
  }

  // ── render ────────────────────────────────────────────────────────────────────────────────────────
  const DOTS = '<span class="hx-sec-dots" aria-label="hidden value">••••••••••••</span>';

  // The whole 1Password setup, one step at a time (same steps as bin/setup's Secrets step). Ticks come from what
  // Tabbie can check: a token is saved (st.token), it connects. (No 1Password CLI needed: Tabbie uses the SDK.)
  const LINKS = { signup: 'https://1password.com/sign-up', app: 'https://1password.com/downloads/mac',
    web: 'https://my.1password.com/' };
  const link = (k, label) => `<a class="hx-st-btn hx-sec-link" href="${LINKS[k]}" target="_blank" rel="noopener noreferrer">${esc(label)}${icon('LinkSquare02Icon', 13)}</a>`;
  function setup(st) {
    const v = esc(st.vault);
    const step = (n, done, title, body, extra) => `<li class="${done ? 'is-done' : ''}"><span class="hx-sec-n">${done ? icon('Tick02Icon', 13) : n}</span><div><b>${title}</b>${body ? `<span>${body}</span>` : ''}${extra ? `<p class="hx-sec-links">${extra}</p>` : ''}</div></li>`;
    const linked = st.token || st.connected;
    return `<div class="hx-st-card hx-sec-setup"><div class="hx-st-main">
      <b class="hx-sec-title">Connect 1Password <span class="hx-st-pill">Optional</span></b>
      <span class="hx-st-dim">Keep API keys in 1Password and let Claude Code and Codex use them while they work, without anyone seeing them: not you in the chat, not the AI. Tabbie only ever shows their names. About 5 minutes.</span>
      <ol class="hx-sec-steps">
        ${step(1, linked, 'Get 1Password', 'A 1Password account and the Mac app, signed in. Any plan works, including Individual and Families.',
          link('signup', 'Sign up') + link('app', 'Download for Mac'))}
        ${step(2, st.connected, `Make a vault called “${v}”`, `In the 1Password app: <b>File → New Vault</b> (or <b>+</b> next to Vaults), name it <b>${v}</b>. Tabbie can only see keys in this vault, never your others.`)}
        ${step(3, st.token || st.connected, 'Create a service account (Tabbie’s access token)',
          `A separate login just for Tabbie, so it never needs your password. At 1Password.com: <b>Developer</b> → <b>Directory</b>, then <b>Other</b> under Infrastructure Secrets Management → <b>Create a Service Account</b>. Name it <b>Tabbie</b>, choose the <b>${v}</b> vault with <b>Read</b> and <b>Write</b> (leave Share off), then <b>Create</b>. Save the token in 1Password when it offers, and copy it: it starts <code>ops_</code> and is shown only once. Its access can’t be changed later; if you get it wrong, make a new one.`,
          link('web', 'Open 1Password.com'))}
        ${step(4, st.connected, 'Paste the token here', 'It goes straight into your Mac’s Keychain. Tabbie checks it can open the vault, and never shows the token again.')}
      </ol>
      <form class="hx-sec-form" data-act="connect" autocomplete="off">
        <input type="password" name="token" placeholder="ops_…" autocomplete="new-password" data-1p-ignore data-lpignore="true" spellcheck="false" required aria-label="Service account token">
        <button type="submit" class="hx-st-btn is-primary">Connect</button>
      </form>
      ${st.error ? `<span class="hx-st-fix">${esc(st.error)}${/vault|permission|access/i.test(st.error) ? ` Check the service account has the “${v}” vault with Read and Write.` : ''}</span>` : ''}
      <span class="hx-st-dim hx-sec-after">Then add keys with <b>Add</b> here, or straight into the “${v}” vault in the 1Password app: Tabbie picks those up too.</span>
    </div></div>`;
  }

  function providerOptions(sel) {
    const ps = (state && state.providers) || {};
    return Object.keys(ps).map((k) => `<option value="${esc(k)}"${k === sel ? ' selected' : ''}>${esc(ps[k].label)}</option>`).join('');
  }

  function form(kind, it) {
    const p = (it && it.provider) || 'other';
    const title = kind === 'add' ? 'Add a secret' : `Replace ${esc(it.title)}`;
    return `<form class="hx-sec-edit" data-act="${kind}" ${it ? `data-id="${esc(it.id)}"` : ''} autocomplete="off">
      <div class="hx-sec-edit-h"><b>${title}</b><button type="button" class="hx-sec-x" data-act="cancel" aria-label="Cancel">${icon('Cancel01Icon', 14)}</button></div>
      ${kind === 'add' ? `<label><span>Provider</span><select name="provider">${providerOptions(p)}</select></label>
      <label><span>Name</span><input name="title" maxlength="80" placeholder="Resend API key" required></label>
      <label><span>Variable</span><input name="env" maxlength="64" placeholder="RESEND_API_KEY" pattern="[A-Za-z][A-Za-z0-9_]{1,63}" spellcheck="false" required></label>` : ''}
      <label><span>Value</span><input type="password" name="value" placeholder="Paste the key" autocomplete="new-password" data-1p-ignore data-lpignore="true" spellcheck="false" required></label>
      <p class="hx-st-note">Goes straight to 1Password. It won’t be shown here again.</p>
      <div class="hx-sec-edit-b"><button type="submit" class="hx-st-btn is-primary">${kind === 'add' ? 'Add' : 'Replace'}</button></div>
    </form>`;
  }

  function row(it) {
    const confirming = mode && mode.id === it.id && mode.kind;
    let side = '';
    if (busyId === it.id) side = '<span class="hx-st-dim hx-sec-busy">Working…</span>';
    else if (confirming === 'delete') side = `<span class="hx-sec-ask">Delete? It moves to 1Password’s archive.</span><button type="button" class="hx-st-btn is-danger" data-act="delete-yes" data-id="${esc(it.id)}">Delete</button><button type="button" class="hx-st-btn" data-act="cancel">Keep</button>`;
    else if (confirming === 'rotate') side = `<span class="hx-sec-ask">${esc(it.rotate_how || 'Make a new key and retire this one?')}</span><button type="button" class="hx-st-btn is-primary" data-act="rotate-yes" data-id="${esc(it.id)}">Rotate</button><button type="button" class="hx-st-btn" data-act="cancel">Cancel</button>`;
    else side = `${it.rotatable ? `<button type="button" class="hx-st-btn" data-act="rotate" data-id="${esc(it.id)}">${icon('RefreshIcon', 13)}Rotate</button>` : ''}
      <button type="button" class="hx-st-btn" data-act="replace" data-id="${esc(it.id)}">Replace</button>
      <button type="button" class="hx-sec-icon" data-act="delete" data-id="${esc(it.id)}" aria-label="Delete ${esc(it.title)}">${icon('Delete02Icon', 15)}</button>`;
    const shown = revealed && revealed.id === it.id ? revealed : null;
    return `<div class="hx-sec-row${confirming ? ' is-asking' : ''}">
      <div class="hx-st-main"><b>${esc(it.title)}</b>
        <span class="hx-sec-meta">${it.env ? `<code>${esc(it.env)}</code>` : '<span class="hx-st-fix">no variable name</span>'}${it.provider !== 'other' && !String(it.title).toLowerCase().includes(String(it.provider_label).toLowerCase()) ? `<span class="hx-st-dim">${esc(it.provider_label)}</span>` : ''}${shown ? '' : DOTS}</span></div>
      <div class="hx-st-side">${side}</div>
      ${shown ? `<div class="hx-sec-once"><div class="hx-sec-once-h">${icon('Key01Icon', 14)}<b>New key</b><span class="hx-st-dim">Shown once. Copy it now: leave or refresh this page and it’s gone.</span></div>
        <div class="hx-sec-once-v"><code class="hx-sec-val">${esc(shown.value)}</code><button type="button" class="hx-st-btn is-primary" data-act="copy">Copy</button><button type="button" class="hx-st-btn" data-act="hide">Done</button></div>
        ${shown.note ? `<p class="hx-st-note">${esc(shown.note)}</p>` : ''}</div>` : ''}
    </div>`;
  }

  function render() {
    if (!box) return;
    if (!state) {
      box.innerHTML = `<section class="hx-st-group"><h3>Secrets</h3>${err ? `<p class="hx-st-note">${esc(err)}</p>` : '<div class="hx-st-card hx-st-skel"><i></i><i></i></div>'}</section>`;
      return;
    }
    const st = state.status || {};
    const items = state.items || [];
    let body;
    if (!st.connected) body = setup(st);
    else {
      body = `<div class="hx-st-card hx-sec-list">${items.length ? items.map(row).join('') : '<p class="hx-st-note hx-sec-empty">No secrets yet. Add your first API key.</p>'}</div>`
        + (mode && mode.kind === 'add' ? form('add') : '')
        + (mode && mode.kind === 'replace' ? form('replace', items.find((i) => i.id === mode.id)) : '');
    }
    box.innerHTML = `<section class="hx-st-group">
      <div class="hx-st-head"><h3>Secrets</h3>${st.connected ? `<span class="hx-sec-head-r"><span class="hx-st-pill is-ok">1Password · ${esc(st.vault)}</span>${mode && mode.kind === 'add' ? '' : '<button type="button" class="hx-st-btn" data-act="add">Add</button>'}</span>` : ''}</div>
      ${err ? `<p class="hx-st-fix hx-sec-err" role="alert">${esc(err)}</p>` : ''}
      ${body}
      ${st.connected ? `<p class="hx-st-note">Claude Code and Codex use these through <code>with-secrets</code>: the key goes into the command they run, never into the chat, a file or the AI’s view. Values never show here; only a freshly rotated key, once.</p>` : ''}
    </section>`;
    const f = box.querySelector('.hx-sec-edit input[name="title"], .hx-sec-edit input[name="value"]');
    if (f && document.activeElement === document.body) f.focus({ preventScroll: true });
  }

  async function load(force, tries) {
    if (!box || (loading && !tries)) return;
    loading = true;
    try { state = await get(force); err = ''; }
    catch (e) {
      if (!e.status) console.warn('secrets: load failed', String(e && e.message || e));
      // right after a page load the helper's proxy can still be warming up: try again a couple of times quietly
      if ((!e.status || e.status >= 502) && (tries || 0) < 4) { setTimeout(() => load(force, (tries || 0) + 1), 1500); return; }
      err = e.status === 404 ? 'Tabbie’s helper is out of date: restart it to manage secrets.' : (e.status === 403 ? 'Allow Tabbie’s helper (Settings → Extensions) to manage secrets.' : 'Couldn’t reach Tabbie’s helper.');
    }
    loading = false;
    render();
  }

  // ── actions ───────────────────────────────────────────────────────────────────────────────────────
  async function act(fn) {
    err = '';
    try { await fn(); } catch (e) { err = e.message; }
    busyId = '';
    render();
  }

  function onClick(e) {
    const b = e.target.closest('[data-act]');
    if (!b || b.tagName === 'FORM') return;
    const a = b.dataset.act, id = b.dataset.id;
    if (a === 'add') { mode = { kind: 'add' }; revealed = null; err = ''; render(); }
    else if (a === 'replace' || a === 'delete' || a === 'rotate') { mode = { kind: a, id }; err = ''; render(); }
    else if (a === 'cancel') { mode = null; err = ''; render(); }
    else if (a === 'hide') { revealed = null; render(); }
    else if (a === 'copy') {
      const v = revealed && revealed.value;
      if (!v) return;
      (navigator.clipboard ? navigator.clipboard.writeText(v) : Promise.reject()).then(() => { b.textContent = 'Copied'; setTimeout(() => { if (b.isConnected) b.textContent = 'Copy'; }, 1500); }, () => { err = 'Copy didn’t work: select the key and copy it.'; render(); });
    } else if (a === 'delete-yes') {
      mode = null; busyId = id; render();
      act(async () => { state.items = (await post('delete', { id })).items; });
    } else if (a === 'rotate-yes') {
      mode = null; busyId = id; render();
      act(async () => {
        const j = await post('rotate', { id });
        revealed = { id, value: j.value, note: j.note };
        state = await get(true);
      });
    }
  }

  function onSubmit(e) {
    const f = e.target.closest('form[data-act]');
    if (!f) return;
    e.preventDefault();
    const data = Object.fromEntries(new FormData(f).entries());
    f.querySelectorAll('input[type="password"]').forEach((i) => { i.value = ''; });  // never left sitting in the page
    const btn = f.querySelector('button[type="submit"]');
    if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }
    const kind = f.dataset.act;
    act(async () => {
      if (kind === 'connect') { state = await post('connect', { token: data.token }); }
      else if (kind === 'add') { state.items = (await post('add', data)).items; mode = null; }
      else if (kind === 'replace') { state.items = (await post('update', { id: f.dataset.id, value: data.value })).items; mode = null; }
    });
  }

  function onInput(e) {
    const f = e.target.closest('form[data-act="add"]');
    if (!f) return;
    const prov = f.querySelector('[name="provider"]'), env = f.querySelector('[name="env"]'), title = f.querySelector('[name="title"]');
    if (e.target === prov) {
      const p = (state.providers || {})[prov.value] || {};
      if (!env.dataset.touched) env.value = p.env || '';
      if (!title.dataset.touched && prov.value !== 'other') title.value = p.label + (/token/i.test(p.env || '') ? ' token' : ' API key');
    }
    if (e.target === env) { env.dataset.touched = '1'; env.value = env.value.toUpperCase().replace(/[^A-Z0-9_]/g, '_'); }
    if (e.target === title) title.dataset.touched = '1';
  }

  // ── mount under Settings → Accounts ───────────────────────────────────────────────────────────────
  function forget() { revealed = null; mode = null; err = ''; }  // a rotated key never outlives the visit
  function mount() {
    const pane = document.getElementById('settingsPaneProviders');
    if (!pane) return;
    if (!box || !box.isConnected) {
      box = document.createElement('div');
      box.id = 'hxSecrets';
      box.addEventListener('click', onClick);
      box.addEventListener('submit', onSubmit);
      box.addEventListener('input', onInput);
      box.addEventListener('change', onInput);
      pane.appendChild(box);
      render();
    } else if (box.nextElementSibling && box.nextElementSibling.id !== 'hxTeam' && !box.contains(document.activeElement)) {
      pane.appendChild(box);  // stays last under Accounts (Team sits right after it); never moved while typing in it
    }
    const on = pane.classList.contains('active') && !document.hidden;
    if (on && !box.dataset.on) { box.dataset.on = '1'; load(false); }
    if (!on && box.dataset.on) { delete box.dataset.on; forget(); render(); }
  }
  addEventListener('pagehide', forget);
  // the pane's class flips when Settings switches section; watch just that (re-found if Settings is rebuilt)
  let watched = null;
  const mo = new MutationObserver(mount);
  setInterval(() => {
    const pane = document.getElementById('settingsPaneProviders');
    if (pane && pane !== watched) { watched = pane; mo.disconnect(); mo.observe(pane, { attributes: true, attributeFilter: ['class'] }); }
    mount();
  }, 800);
  document.addEventListener('visibilitychange', mount);
  mount();
})();
