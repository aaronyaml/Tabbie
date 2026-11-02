/* Tabbie — "Notify this device" (Web Push). Adds a row under Settings → Browser notifications: one tap
 * subscribes this phone / browser, and Tabbie pings it when a job finishes or needs you, even with Tabbie
 * closed. On iPhone it works from the home-screen app (iOS 16.4+). The push is empty; push-sw.js asks
 * Tabbie what happened. Subscriptions live on the Mac (sidecar /push/*), never in git.
 */
(() => {
  'use strict';
  if (window.__hxPush) return;
  window.__hxPush = true;

  const API = 'api/extensions/cc-agents/sidecar/push/';
  const SW = 'extensions/push-sw.js';
  const SCOPE = 'extensions/';
  const supported = () => 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
  const standalone = () => matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
  const ios = () => /iPhone|iPad|iPod/.test(navigator.userAgent);

  const b64 = (s) => { const p = '='.repeat((4 - (s.length % 4)) % 4); const r = atob((s + p).replace(/-/g, '+').replace(/_/g, '/'));
    return Uint8Array.from(r, (c) => c.charCodeAt(0)); };
  async function call(path, body) {
    const r = await fetch(API + path, body ? { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
      : { credentials: 'same-origin' });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return r.json();
  }
  async function reg() {
    return (await navigator.serviceWorker.getRegistration(SCOPE)) || navigator.serviceWorker.register(SW, { scope: SCOPE });
  }
  // this registration active (serviceWorker.ready would wait on the page's own scope, upstream's sw.js)
  async function active(r) {
    const w = r.installing || r.waiting;
    if (r.active || !w) return r;
    await new Promise((ok) => { w.addEventListener('statechange', () => { if (w.state === 'activated') ok(); }); setTimeout(ok, 8000); });
    return r;
  }
  const device = () => (ios() ? 'iPhone' : /Android/.test(navigator.userAgent) ? 'Android' : /Mac/.test(navigator.userAgent) ? 'Mac' : 'Browser');

  let row = null, busy = false;
  async function paint() {
    if (!row) return;
    const btn = row.querySelector('button'), note = row.querySelector('.hx-push-note');
    if (!supported()) {
      btn.hidden = true;
      note.textContent = ios() && !standalone() ? 'To get these on iPhone, open Tabbie from your home screen first.' : "This browser can't receive push notifications.";
      return;
    }
    let sub = null, st = {};
    try { sub = await (await reg()).pushManager.getSubscription(); } catch (_) {}
    try { st = await call('key?endpoint=' + encodeURIComponent(sub ? sub.endpoint : '')); } catch (_) {}
    if (!st.available) { btn.hidden = true; note.textContent = 'Push is off on the Mac (the sidecar is missing pywebpush).'; return; }
    btn.hidden = false;
    const on = !!(sub && st.this_device);
    btn.textContent = on ? 'Turn off on this device' : 'Notify this device';
    btn.dataset.on = on ? '1' : '';
    note.textContent = on ? `On. Tabbie pings this ${device()} when a job finishes or needs you.`
      : `Get a notification when a job finishes or needs you, even with Tabbie closed${st.devices ? ` (${st.devices} device${st.devices === 1 ? '' : 's'} on)` : ''}.`;
  }
  // Subscribe this device. Call straight from a tap: Safari only shows the permission prompt then.
  async function turnOn() {
    const perm = Notification.requestPermission();
    const r = await active(await reg());
    if ((await perm) !== 'granted') throw new Error('Notifications are blocked for Tabbie in this browser’s settings');
    let sub = await r.pushManager.getSubscription();
    const { key } = await call('key');
    if (!sub) sub = await r.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64(key) });
    await call('subscribe', { subscription: sub.toJSON(), device: device() });
  }
  async function toggle() {
    if (busy) return;
    busy = true;
    const note = row.querySelector('.hx-push-note');
    const turningOn = !row.querySelector('button').dataset.on;
    try {
      if (turningOn) await turnOn();
      else {
        const sub = await (await reg()).pushManager.getSubscription();
        if (sub) { await call('unsubscribe', { endpoint: sub.endpoint }); await sub.unsubscribe(); }
      }
      await paint();
    } catch (e) {
      note.textContent = "Couldn't change it: " + (e && e.message ? e.message : e);
    } finally { busy = false; }
  }

  // Home card on phones, once: the setting lives deep in Settings and iOS only asks after a tap.
  const DISMISS = 'hxpush-card';
  const phone = () => matchMedia('(max-width: 640px)').matches;
  let cardState = null;  // 'ask' | 'install' | null (nothing to say)
  async function cardCheck() {
    try { if (localStorage.getItem(DISMISS)) return; } catch (_) {}
    if (!phone()) return;
    if (!supported()) { if (ios() && !standalone()) cardState = 'install'; return; }
    if (Notification.permission === 'denied') return;
    try {
      const sub = await (await reg()).pushManager.getSubscription();
      const st = await call('key?endpoint=' + encodeURIComponent(sub ? sub.endpoint : ''));
      if (st.available && !(sub && st.this_device)) cardState = 'ask';
    } catch (_) {}
  }
  function dismiss() { try { localStorage.setItem(DISMISS, '1'); } catch (_) {} cardState = null; const c = document.querySelector('.hx-push-card'); if (c) c.remove(); }
  function placeCard() {
    const home = document.getElementById('hxHome');
    let card = document.querySelector('.hx-push-card');
    if (!cardState || !home || !home.offsetParent) { if (card && !cardState) card.remove(); return; }
    if (card && card.nextElementSibling === home) return;
    if (!card) {
      card = document.createElement('div');
      card.className = 'hx-push-card';
      card.innerHTML = cardState === 'ask'
        ? '<div class="hx-push-card-tx"><b>Get notified on this phone</b><span>When a job finishes or needs you, even with Tabbie closed.</span></div>'
          + '<button type="button" class="hx-push-card-on">Turn on</button><button type="button" class="hx-push-card-x" aria-label="Not now">×</button>'
        : '<div class="hx-push-card-tx"><b>Want notifications?</b><span>Add Tabbie to your Home Screen (Share → Add to Home Screen), open it from there, and turn them on.</span></div>'
          + '<button type="button" class="hx-push-card-x" aria-label="Dismiss">×</button>';
      card.addEventListener('click', async (e) => {
        if (e.target.closest('.hx-push-card-x')) return dismiss();
        const on = e.target.closest('.hx-push-card-on');
        if (!on || busy) return;
        busy = true;
        on.textContent = 'Turning on…';
        try {
          await turnOn();
          card.innerHTML = '<div class="hx-push-card-tx"><b>Notifications are on</b><span>Change it any time in Settings → Preferences.</span></div>';
          try { localStorage.setItem(DISMISS, '1'); } catch (_) {}
          cardState = null;
          setTimeout(() => card.remove(), 4000);
        } catch (err) {
          on.textContent = 'Turn on';
          card.querySelector('span').textContent = String(err && err.message ? err.message : err);
        } finally { busy = false; }
      });
    }
    home.before(card);
  }

  function place() {
    const anchor = document.getElementById('notificationPermissionStatus');
    const field = anchor && anchor.closest('.settings-field');
    if (!field || field.querySelector('.hx-push')) return;
    row = document.createElement('div');
    row.className = 'hx-push';
    row.innerHTML = '<button type="button" class="sm-btn" hidden>Notify this device</button><div class="hx-push-note"></div>';
    row.querySelector('button').addEventListener('click', toggle);
    field.appendChild(row);
    paint();
  }
  // Settings is rendered on open: re-add the row when it appears (shared throttled observer)
  const check = () => { if (document.getElementById('notificationPermissionStatus') && !document.querySelector('.hx-push')) place(); placeCard(); };
  function boot() {
    place();
    cardCheck().then(placeCard);
    if (window.hxDom && window.hxDom.on) window.hxDom.on(check); else setInterval(check, 1500);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, { once: true }); else boot();
})();
