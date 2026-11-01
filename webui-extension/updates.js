/* Tabbie — update card. Asks before downloading a new version from GitHub, then finishes it.
 * Data: cc-agents sidecar via the WebUI's authenticated proxy:
 *   GET  …/sidecar/update          -> {available, ahead_by, commits, remote, pending_restart}
 *   POST …/sidecar/update          -> download it (fast-forward; local edits are never overwritten)
 *   POST …/sidecar/update/restart  -> restart what it touched, only when no request is running
 * "Later" hides the card until GitHub has something newer than what you dismissed.
 */
(() => {
  'use strict';
  if (window.__hxUpdates) return;
  window.__hxUpdates = true;

  const API = 'api/extensions/cc-agents/sidecar/update';
  const KEY = 'hx-update-dismissed';
  const EVERY = 6 * 3600 * 1000;
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const get = (k) => { try { return localStorage.getItem(k); } catch (_) { return null; } };
  const set = (k, v) => { try { localStorage.setItem(k, v); } catch (_) {} };

  async function call(path, method) {
    const url = API + path;
    if (method === 'POST' && typeof window.api === 'function') {
      return window.api('/' + url, { method: 'POST', body: '{}', headers: { 'Content-Type': 'application/json' }, retries: 0, timeoutToast: false });
    }
    const r = await fetch(url, { method: method || 'GET', credentials: 'same-origin', cache: 'no-store',
      headers: method === 'POST' ? { 'Content-Type': 'application/json' } : undefined, body: method === 'POST' ? '{}' : undefined });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || String(r.status));
    return j;
  }

  let card = null;
  function show(html) {
    if (!card) {
      card = document.createElement('div');
      card.id = 'hxUpdate';
      card.setAttribute('role', 'status');
      card.addEventListener('click', onClick);
      document.body.appendChild(card);
    }
    card.innerHTML = html;
    requestAnimationFrame(() => card.classList.add('is-in'));
  }
  function hide() { if (card) { card.classList.remove('is-in'); setTimeout(() => { if (card && !card.classList.contains('is-in')) card.remove(), card = null; }, 250); } }

  let info = null;
  function offer(j) {
    info = j;
    const n = j.ahead_by || (j.commits || []).length;
    const list = (j.commits || []).slice(0, 6).map((c) => `<li>${esc(c.title)}</li>`).join('');
    show(`<div class="hx-up-head"><b>Tabbie update available</b><span>${n ? `${n} change${n === 1 ? '' : 's'}` : 'New version'}</span></div>`
      + (list ? `<ul class="hx-up-list">${list}</ul>` : '')
      + `<p class="hx-up-note">Your settings and any files you've edited stay as they are.</p>`
      + `<div class="hx-up-btns"><button type="button" data-later>Later</button><button type="button" class="hx-up-go" data-update>Update</button></div>`);
  }
  function finished(res) {
    const needs = res.needs || [];
    const restart = needs.some((x) => x === 'gateway' || x === 'sidecar' || x === 'caddy');
    const setup = needs.includes('setup') ? '<p class="hx-up-note">This update changed setup steps: run <code>bin/setup</code> on the Mac when convenient.</p>' : '';
    if (restart) {
      show(`<div class="hx-up-head"><b>Update downloaded</b>${res.from ? `<span>${esc(res.from)} → ${esc(res.to)}</span>` : ''}</div>`
        + `<p class="hx-up-note">Restart Tabbie's services to finish. It waits if a request is running. A chat reply being written right now would stop.</p>${setup}`
        + `<div class="hx-up-btns"><button type="button" data-later-restart>Later</button><button type="button" class="hx-up-go" data-restart>Restart now</button></div>`);
    } else {
      show(`<div class="hx-up-head"><b>Update downloaded</b>${res.from ? `<span>${esc(res.from)} → ${esc(res.to)}</span>` : ''}</div>${setup}`
        + `<div class="hx-up-btns"><button type="button" class="hx-up-go" data-reload>Reload page</button></div>`);
    }
  }
  function error(msg, retry) {
    show(`<div class="hx-up-head"><b>Couldn't update</b></div><p class="hx-up-note hx-up-err">${esc(msg)}</p>`
      + `<div class="hx-up-btns"><button type="button" data-close>Close</button>${retry ? `<button type="button" class="hx-up-go" data-${retry}>Try again</button>` : ''}</div>`);
  }
  const busy = (label) => { const b = card && card.querySelector('.hx-up-go'); if (b) { b.disabled = true; b.textContent = label; } };

  async function onClick(e) {
    const t = e.target.closest('button');
    if (!t) return;
    if (t.hasAttribute('data-later')) { if (info) set(KEY, info.remote); hide(); return; }
    if (t.hasAttribute('data-close') || t.hasAttribute('data-later-restart')) { hide(); return; }
    if (t.hasAttribute('data-reload')) { location.reload(); return; }
    if (t.hasAttribute('data-update')) {
      busy('Downloading…');
      try { finished(await call('', 'POST')); } catch (err) { error(err.message || 'Try again later.', 'update'); }
      return;
    }
    if (t.hasAttribute('data-restart')) {
      busy('Restarting…');
      try {
        await call('/restart', 'POST');
        setTimeout(() => location.reload(), 4000);  // gateway + sidecar come back in a few seconds
      } catch (err) { error(err.message || 'Try again later.', 'restart'); }
    }
  }

  async function check(force) {
    let j;
    try { j = await call(force ? '?force=1' : '', 'GET'); } catch (_) { return; }  // no consent / sidecar down: stay quiet
    if (j.pending_restart && j.pending_restart.length) { finished({ needs: j.pending_restart }); return; }
    if (!j.available || get(KEY) === j.remote) return;
    offer(j);
  }

  window.hxUpdates = { check: () => check(true) };
  setTimeout(() => check(false), 4000);  // after the app has settled
  setInterval(() => check(false), EVERY);
})();
