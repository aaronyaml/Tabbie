/* Tabbie — asks for anything Tabbie needs and doesn't have yet (plain JS, no build step).
 * The list lives in plugin/command-centre/needs.py (sidecar GET/POST /needs): your first name today,
 * whatever a future update adds tomorrow. On load, if something is missing, one small sheet asks for it;
 * saving reloads the page so everything picks the answer up. Nothing missing: nothing shows.
 */
(() => {
  'use strict';
  if (window.__hxNeeds) return;
  window.__hxNeeds = true;
  const API = 'api/extensions/cc-agents/sidecar/needs';
  const esc = (t) => String(t == null ? '' : t).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  function show(list) {
    if (!list.length || document.querySelector('.hx-needs')) return;
    const one = list.length === 1;
    const el = document.createElement('div');
    el.className = 'hx-needs';
    el.innerHTML = `<div class="hx-needs-scrim"></div>
      <form class="hx-needs-card" role="dialog" aria-modal="true" aria-labelledby="hxNeedsT" novalidate>
        <h2 id="hxNeedsT">${esc(one ? list[0].title : 'A couple of things before you start')}</h2>
        ${one ? `<p>${esc(list[0].hint || '')}</p>` : ''}
        ${list.map((n) => `<label class="hx-needs-f">${one ? '' : `<span>${esc(n.title)}</span>`}
          <input name="${esc(n.key)}" type="text" maxlength="${+n.max || 80}" placeholder="${esc(n.placeholder || '')}"
            autocomplete="${esc(n.autocomplete || 'off')}" aria-label="${esc(n.label || n.title)}" required>
          ${one ? '' : `<small>${esc(n.hint || '')}</small>`}</label>`).join('')}
        <p class="hx-needs-err" role="alert" hidden></p>
        <button type="submit" class="hx-needs-go">Continue</button>
      </form>`;
    document.body.appendChild(el);
    const form = el.querySelector('form');
    const err = el.querySelector('.hx-needs-err');
    const first = form.querySelector('input');
    setTimeout(() => first && first.focus(), 50);
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const values = {};
      for (const i of form.querySelectorAll('input')) {
        if (!i.value.trim()) { err.textContent = `Fill in ${i.getAttribute('aria-label')}`; err.hidden = false; i.focus(); return; }
        values[i.name] = i.value.trim();
      }
      const btn = form.querySelector('.hx-needs-go');
      btn.disabled = true; btn.textContent = 'Saving…';
      try {
        const r = await fetch(API, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ values }) });
        const j = await r.json().catch(() => ({}));
        if (!r.ok || !j.ok) throw new Error(j.error || 'Couldn’t save that. Try again.');
        location.reload();
      } catch (x) {
        err.textContent = x.message; err.hidden = false;
        btn.disabled = false; btn.textContent = 'Continue';
      }
    });
  }

  let tries = 0;
  function check() {
    fetch(API, { credentials: 'same-origin' }).then((r) => (r.ok ? r.json() : null))
      .then((j) => { if (j && Array.isArray(j.needs)) show(j.needs); else if (++tries < 5) setTimeout(check, 4000); })
      .catch(() => { if (++tries < 5) setTimeout(check, 4000); });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => setTimeout(check, 600));
  else setTimeout(check, 600);
})();
