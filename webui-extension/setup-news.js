/* Tabbie — "New in setup" card (plain JS, no build step). Pairs with setup-news.css.
 * When an update brings a setup step this Mac hasn't been through (hmem/setup_news.py: e.g. Secrets with
 * 1Password), a small card offers it once: "Set up" opens the place to do it (a Settings section), "Not now"
 * hides it for good. Optional steps say so. A step already set up some other way is never offered.
 *   GET  …/sidecar/setup-news       -> {steps: [{id, title, blurb, optional, open, terminal}]}
 *   POST …/sidecar/setup-news/seen  {id}
 * It waits for the update card (#hxUpdate) to go away first, and never shows on top of it.
 */
(() => {
  'use strict';
  if (window.__hxSetupNews) return;
  window.__hxSetupNews = true;

  const API = 'api/extensions/cc-agents/sidecar/setup-news';
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const icon = (n, s) => (typeof window.hxIcon === 'function' ? window.hxIcon(n, s) : '');
  let card = null, step = null;

  async function seen(id) {
    try {
      await fetch(API + '/seen', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id }) });
    } catch (_) {}
  }
  function hide() {
    if (!card) return;
    card.classList.remove('is-in');
    const c = card; card = null;
    setTimeout(() => c.remove(), 220);
  }
  function open(target) {
    const [kind, where] = String(target || '').split(':');
    if (kind !== 'settings') return;
    try {
      if (typeof window.switchPanel === 'function') window.switchPanel('settings');
      const section = { secrets: 'providers' }[where] || where;
      setTimeout(() => {
        if (typeof window.switchSettingsSection === 'function') window.switchSettingsSection(section);
        // the section's own block, once it has drawn (Secrets loads from 1Password)
        let n = 0;
        const iv = setInterval(() => {
          const el = document.getElementById({ secrets: 'hxSecrets' }[where] || '');
          if (el || ++n > 75) { clearInterval(iv); if (el) el.scrollIntoView({ block: 'start', behavior: 'smooth' }); }
        }, 200);
      }, 60);
    } catch (_) {}
  }
  function show(s) {
    step = s;
    card = document.createElement('div');
    card.id = 'hxSetupNews';
    card.setAttribute('role', 'status');
    card.innerHTML = `<div class="hx-sn-head"><span class="hx-sn-ico">${icon('SparklesIcon', 16)}</span><b>New in Tabbie setup</b>`
      + `${s.optional ? '<span class="hx-sn-tag">Optional</span>' : ''}</div>`
      + `<p class="hx-sn-title">${esc(s.title)}</p><p class="hx-sn-blurb">${esc(s.blurb)}</p>`
      + `<div class="hx-sn-btns"><button type="button" data-no>Not now</button><button type="button" class="hx-sn-go" data-go>Set up</button></div>`;
    card.addEventListener('click', (e) => {
      const b = e.target.closest('button');
      if (!b) return;
      seen(step.id);
      if (b.hasAttribute('data-go')) open(step.open);
      hide();
    });
    document.body.appendChild(card);
    requestAnimationFrame(() => requestAnimationFrame(() => card && card.classList.add('is-in')));
  }

  let tries = 0;
  async function check() {
    if (card) return;
    let j;
    try {
      const r = await fetch(API, { credentials: 'same-origin', cache: 'no-store' });
      if (!r.ok) throw new Error(String(r.status));
      j = await r.json();
    } catch (_) { if (++tries < 4) setTimeout(check, 15000); return; }
    const s = (j.steps || [])[0];
    if (!s) return;
    // never on top of the update card, the helper-consent card or a sign-in card: wait for them
    const busy = () => document.querySelector('#hxUpdate.is-in, #hxHelperConsent, #hxHubSignin');
    const go = () => { if (busy()) { setTimeout(go, 5000); return; } show(s); };
    go();
  }
  const start = () => setTimeout(check, 4000);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
