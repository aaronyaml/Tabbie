/* Tabbie — "Allow Tabbie's helper" card. Tabbie's panels (Projects, the model picker, Motion, updates,
 * sign-in) reach their helper on this Mac (the cc-agents sidecar, 127.0.0.1:8794) through the WebUI's
 * proxy, which upstream only opens after a one-time consent hidden in Settings → Extensions. Until
 * then every panel failed with a permission error. This asks once, in place: Allow sends the same
 * consent the Extensions panel would (POST api/extensions/sidecar-proxy-consent), then reloads.
 */
(() => {
  'use strict';
  if (window.__hxHelperConsent) return;
  window.__hxHelperConsent = true;

  const EXT = 'cc-agents';
  const PATH = `/api/extensions/${EXT}/sidecar/`;
  let card = null;

  async function needed() {
    try {
      const r = await fetch('api/extensions/status', { credentials: 'same-origin', cache: 'no-store' });
      if (!r.ok) return false;
      const d = await r.json();
      const sc = (d.sidecars || []).find((s) => s && s.proxy && s.proxy.path === PATH);
      return !!(sc && sc.proxy.consent_required);
    } catch (_) { return false; }
  }

  async function allow(btn) {
    btn.disabled = true; btn.textContent = 'Allowing…';
    try {
      const body = JSON.stringify({ id: EXT, approved: true });
      if (typeof window.api === 'function') await window.api('/api/extensions/sidecar-proxy-consent', { method: 'POST', body });
      else {
        const r = await fetch('api/extensions/sidecar-proxy-consent', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body });
        if (!r.ok) throw new Error(String(r.status));
      }
      location.reload();
    } catch (e) {
      btn.disabled = false; btn.textContent = 'Try again';
      card.querySelector('.hx-hc-err').textContent = `Couldn't allow it (${(e && e.message) || e}). You can also do it in Settings → Extensions → Agents & questions.`;
    }
  }

  function show() {
    if (card) return;
    card = document.createElement('div');
    card.id = 'hxHelperConsent';
    card.setAttribute('role', 'dialog');
    card.setAttribute('aria-labelledby', 'hxHcTitle');
    card.innerHTML = `<b id="hxHcTitle">Allow Tabbie's helper</b>
      <p>Projects, the model picker, Motion and updates talk to a small helper running on this Mac. Allow it once to turn them on.</p>
      <p class="hx-hc-err"></p>
      <div class="hx-hc-btns"><button type="button" data-later>Later</button><button type="button" class="hx-hc-go" data-allow>Allow</button></div>`;
    card.addEventListener('click', (e) => {
      const t = e.target.closest('button');
      if (!t) return;
      if (t.hasAttribute('data-allow')) allow(t);
      else { card.remove(); card = null; }
    });
    document.body.appendChild(card);
  }

  async function check() { if (!card && await needed()) show(); }
  window.hxHelperConsent = { open: () => check().then(() => { if (!card) show(); }) };
  setTimeout(check, 1200);
})();
