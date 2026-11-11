/* Tabbie — update card. Asks before downloading a new version from GitHub, then finishes it.
 * Data: cc-agents sidecar via the WebUI's authenticated proxy:
 *   GET  …/sidecar/update          -> {available, ahead_by, commits, remote, pending_restart}
 *   POST …/sidecar/update          -> download it (fast-forward; local edits are never overwritten)
 *   POST …/sidecar/update/restart  -> restart what it touched now, or once nothing is running ({waiting: true})
 * A background check never pops a card: it puts a dot on Settings, and Settings → System shows
 * "See update" (the card). "Later" there snoozes it for a day; "Later" on a downloaded update
 * that still needs a restart snoozes it for 4 h. A scheduled restart is watched (30 s poll) until it has
 * happened, then a "Tabbie restarted · Reload" card appears. Settings → System has a
 * "Tabbie" row with the version and a Check for updates button, any time.
 */
(() => {
  'use strict';
  if (window.__hxUpdates) return;
  window.__hxUpdates = true;

  const API = 'api/extensions/cc-agents/sidecar/update';
  const KEY = 'hx-update-dismissed';
  const EVERY = 30 * 60 * 1000;
  const SOON = 10 * 60 * 1000;
  const SNOOZE = 24 * 3600 * 1000;
  const RKEY = 'hx-update-restart-later';   // {sig, until}: "Later" on the restart card
  const WKEY = 'hx-update-restart-watch';   // set while a scheduled restart is pending (survives a reload)
  const RSNOOZE = 4 * 3600 * 1000;
  const WATCH = 30 * 1000;
  // dismissed this exact version within the last day (older builds stored just the commit: not snoozed)
  const snoozed = (remote) => { try { const d = JSON.parse(get(KEY) || 'null'); return !!d && d.remote === remote && Date.now() < d.until; } catch (_) { return false; } };
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const get = (k) => { try { return localStorage.getItem(k); } catch (_) { return null; } };
  const set = (k, v) => { try { localStorage.setItem(k, v); } catch (_) {} };
  const del = (k) => { try { localStorage.removeItem(k); } catch (_) {} };
  const sigOf = (needs) => (needs || []).slice().sort().join(',');
  const restartSnoozed = (needs) => { try { const d = JSON.parse(get(RKEY) || 'null'); return !!d && d.sig === sigOf(needs) && Date.now() < d.until; } catch (_) { return false; } };

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
    const all = j.commits || [];
    const more = Math.max(0, n - 6, all.length - 6);
    const list = all.slice(0, 6).map((c) => `<li>${esc(c.title)}</li>`).join('')
      + (more ? `<li class="hx-up-more">+${more} more</li>` : '');
    show(`<div class="hx-up-head"><b>Tabbie update available</b><span>${n ? `${n} change${n === 1 ? '' : 's'}` : 'New version'}</span></div>`
      + (list ? `<ul class="hx-up-list">${list}</ul>` : '')
      + `<p class="hx-up-note">Files you've edited that the update also changes are saved to <code>backups/</code>, then replaced.</p>`
      + `<div class="hx-up-btns"><button type="button" data-later>Later</button><button type="button" class="hx-up-go" data-update>Update</button></div>`);
  }
  let lastNeeds = [];
  function finished(res) {
    const needs = res.needs || [];
    lastNeeds = needs;
    const restart = needs.some((x) => x === 'gateway' || x === 'webui' || x === 'sidecar' || x === 'caddy');
    const saved = res.backed_up && res.backed_up.files ? `<p class="hx-up-note">Your edits to ${res.backed_up.files.length} file${res.backed_up.files.length === 1 ? '' : 's'} were saved to <code>${esc(res.backed_up.dir)}</code>.</p>` : '';
    const setup = saved + (needs.includes('setup') ? '<p class="hx-up-note">This update changed setup steps: run <code>bin/setup</code> on the computer when convenient.</p>' : '');
    if (restart) {
      show(`<div class="hx-up-head"><b>Update downloaded</b>${res.from ? `<span>${esc(res.from)} → ${esc(res.to)}</span>` : ''}</div>`
        + `<p class="hx-up-note">Restart Tabbie's services to finish. If anything is running (a request, a worker or a Motion render) it waits until that's done, so nothing is cut off.</p>${setup}`
        + `<div class="hx-up-btns"><button type="button" data-later-restart>Later</button><button type="button" class="hx-up-go" data-restart>Restart now</button></div>`);
    } else {
      show(`<div class="hx-up-head"><b>Update downloaded</b>${res.from ? `<span>${esc(res.from)} → ${esc(res.to)}</span>` : ''}</div>${setup}`
        + `<div class="hx-up-btns"><button type="button" class="hx-up-go" data-reload>Reload page</button></div>`);
    }
  }
  function waitingCard() {
    show(`<div class="hx-up-head"><b>Restart scheduled</b></div><p class="hx-up-note">Tabbie is busy (a request, a worker or a Motion render), so nothing is restarted now. It restarts by itself a minute or so after everything finishes. Keep working.</p>`
      + `<div class="hx-up-btns"><button type="button" data-close>OK</button></div>`);
    watchRestart();
  }

  // ---- a scheduled restart: poll until it has happened, then offer a reload ----
  let watchT = null, sawDown = false;
  function watchRestart() {
    set(WKEY, String(Date.now()));
    sawDown = false;
    clearTimeout(watchT);
    watchT = setTimeout(pollRestart, WATCH);
  }
  async function pollRestart() {
    watchT = null;
    // a stale flag (over a day old) is dropped rather than watched forever
    const since = Number(get(WKEY) || 0);
    if (!since || Date.now() - since > 24 * 3600 * 1000) { del(WKEY); return; }
    let j = null;
    try { j = await call('', 'GET'); } catch (_) { sawDown = true; }  // services restarting: keep polling
    if (j && (!(j.pending_restart && j.pending_restart.length) || sawDown)) { del(WKEY); restarted(); return; }
    watchT = setTimeout(pollRestart, WATCH);
  }
  const composerEmpty = () => { const m = document.getElementById('msg'); return !m || !m.value.trim(); };
  const pageBusy = () => { try { return !!(window.S && window.S.busy); } catch (_) { return false; } };
  function restarted() {
    if (document.hidden && composerEmpty() && !pageBusy()) { location.reload(); return; }
    if (card) { card.remove(); card = null; }
    show(`<div class="hx-up-head"><b>Tabbie restarted</b></div><p class="hx-up-note">The update is finished. Reload to use the new version.</p>`
      + `<div class="hx-up-btns"><button type="button" data-close>Not now</button><button type="button" class="hx-up-go" data-reload>Reload</button></div>`);
  }

  function error(msg, retry) {
    show(`<div class="hx-up-head"><b>Couldn't update</b></div><p class="hx-up-note hx-up-err">${esc(msg)}</p>`
      + `<div class="hx-up-btns"><button type="button" data-close>Close</button>${retry ? `<button type="button" class="hx-up-go" data-${retry}>Try again</button>` : ''}</div>`);
  }
  const busy = (label) => { const b = card && card.querySelector('.hx-up-go'); if (b) { b.disabled = true; b.textContent = label; } };

  async function onClick(e) {
    const t = e.target.closest('button');
    if (!t) return;
    if (t.hasAttribute('data-later')) { if (info) set(KEY, JSON.stringify({ remote: info.remote, until: Date.now() + SNOOZE })); hide(); return; }
    if (t.hasAttribute('data-later-restart')) { set(RKEY, JSON.stringify({ sig: sigOf(lastNeeds), until: Date.now() + RSNOOZE })); hide(); return; }
    if (t.hasAttribute('data-close')) { hide(); return; }
    if (t.hasAttribute('data-reload')) { location.reload(); return; }
    if (t.hasAttribute('data-update')) {
      busy('Downloading…');
      try { finished(await call('', 'POST')); } catch (err) { error(err.message || 'Try again later.', 'update'); }
      return;
    }
    if (t.hasAttribute('data-restart')) {
      busy('Restarting…');
      try {
        const r = await call('/restart', 'POST');
        if (r && r.waiting) { waitingCard(); return; }
        // gateway, WebUI and sidecar come back in a few seconds: reload once the WebUI answers again
        const back = async (n) => {
          try { const x = await fetch('api/extensions/status', { credentials: 'same-origin', cache: 'no-store' }); if (x.ok || n > 20) return location.reload(); } catch (_) {}
          setTimeout(() => back(n + 1), 2000);
        };
        setTimeout(() => back(0), 6000);
      } catch (err) { error(err.message || 'Try again later.', 'restart'); }
    }
  }

  let last = 0;
  async function check(force) {
    if (card) return;  // already showing something
    last = Date.now();
    let j;
    try { j = await call(force ? '?force=1' : '', 'GET'); }
    catch (err) { console.warn('[Tabbie updates] check failed:', err && err.message); return; }  // sidecar down / not allowed yet
    if (j.note) console.info('[Tabbie updates]', j.note);
    if (j.pending_restart && j.pending_restart.length) {
      if (j.restart_waiting) { if (!watchT) watchRestart(); return; }  // scheduled: watch quietly (also after a reload)
      if (force || !restartSnoozed(j.pending_restart)) finished({ needs: j.pending_restart });
      return;
    }
    if (get(WKEY)) {  // the scheduled restart happened before this page loaded: it already runs the new version
      del(WKEY);
      if (typeof window.showToast === 'function') window.showToast('Tabbie restarted · update finished', 3500);
      return;
    }
    if (!j.available) {
      // A copy that can't fast-forward says so once per GitHub version, so it isn't silently stuck.
      if (j.note && j.remote && /can't update automatically/.test(j.note) && !snoozed(j.remote)) {
        info = j;
        show(`<div class="hx-up-head"><b>Tabbie update available</b></div><p class="hx-up-note">${esc(j.note)} Run <code>bin/hub update</code> on the computer for details.</p>`
          + `<div class="hx-up-btns"><button type="button" data-later>OK</button></div>`);
      }
      return;
    }
    if (force) { offer(j); return; }
    // Passive check: no card over the composer. A dot on Settings, and Settings → System offers it.
    badge(j);
  }

  // Update available (found by the background check): a small dot on the Settings icons + the
  // Settings → System row gets an Update button. Nothing covers the page.
  function badge(j) {
    info = j;
    document.querySelectorAll('[data-panel="settings"]').forEach((b) => {
      if (b.querySelector('.hx-up-dot')) return;
      const d = document.createElement('span');
      d.className = 'hx-up-dot';
      d.setAttribute('aria-hidden', 'true');
      b.appendChild(d);
      if (!/update/i.test(b.getAttribute('aria-label') || '')) b.setAttribute('aria-label', (b.getAttribute('aria-label') || 'Settings') + ' (update available)');
    });
    paintSettings(j);
  }
  let paintSettings = () => {};

  // Settings → System: a Tabbie row (version, Check for updates). Upstream's block there covers the
  // WebUI and Hermes Agent, not Tabbie.
  function mountSettings() {
    const pane = document.getElementById('settingsPaneSystem');
    if (!pane || document.getElementById('hxUpdSettings')) return;
    const head = pane.querySelector('.settings-section-head');
    const row = document.createElement('div');
    row.id = 'hxUpdSettings';
    row.innerHTML = `<div><b>Tabbie</b><span data-ver>Checking version…</span></div>`
      + `<button type="button" class="settings-action-btn" data-check>Check for updates</button>`;
    (head ? head.after(row) : pane.prepend(row));
    const ver = row.querySelector('[data-ver]'), btn = row.querySelector('[data-check]');
    const say = (j) => {
      const v = j.local ? j.local.slice(0, 7) : '';
      ver.textContent = j.available ? `Version ${v} · an update is available` : j.note ? `Version ${v} · ${j.note}` : `Version ${v} · up to date`;
      if (j.available) { btn.textContent = 'See update'; btn.dataset.offer = '1'; info = j; }
    };
    paintSettings = say;
    call('', 'GET').then(say).catch(() => { ver.textContent = 'Allow Tabbie\'s helper to check for updates.'; });
    btn.addEventListener('click', async () => {
      if (btn.dataset.offer && info && info.available) { if (card) { card.remove(); card = null; } offer(info); return; }
      btn.disabled = true; btn.textContent = 'Checking…';
      try {
        const j = await call('?force=1', 'GET');
        say(j);
        const fresh = () => { if (card) { card.remove(); card = null; } };
        if (j.pending_restart && j.pending_restart.length && !j.restart_waiting) { fresh(); finished({ needs: j.pending_restart }); }
        else if (j.available) { fresh(); offer(j); }
      } catch (e) { ver.textContent = `Couldn't check (${(e && e.message) || e}).`; }
      btn.disabled = false; btn.textContent = btn.dataset.offer ? 'See update' : 'Check for updates';
    });
  }

  window.hxUpdates = { check: () => check(true) };
  mountSettings();
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mountSettings);
  setTimeout(() => check(false), 4000);  // after the app has settled
  setInterval(() => check(false), EVERY);
  // Coming back to the tab (a phone unlocking, a laptop waking) checks again after a while.
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && Date.now() - last > SOON) check(false);
  });
})();
