/* Tabbie — push service worker (scope /extensions/, separate from upstream's sw.js at /).
 * A push arrives EMPTY (only a ping goes through Apple / Google / Mozilla). On each one it asks Tabbie,
 * behind its login, what just happened (sidecar /push/latest) and shows that: "<chat>: done", "<chat>:
 * needs your answer", ... Tapping it opens (or focuses) that chat.
 */
'use strict';
const BASE = new URL('../', self.location).href;  // the WebUI root, whatever path it's served under
const LATEST = BASE + 'api/extensions/cc-agents/sidecar/push/latest';
const ICON = BASE + 'static/favicon-192.png';

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));

self.addEventListener('push', (e) => e.waitUntil((async () => {
  let ev = null;
  try {
    const r = await fetch(LATEST, { credentials: 'include', cache: 'no-store' });
    if (r.ok) ev = ((await r.json()).events || [])[0] || null;
  } catch (_) {}
  // a visible, focused Tabbie window already shows it: still notify (Safari requires one per push), quietly
  const wins = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
  const watching = wins.some((c) => c.visibilityState === 'visible' && c.focused);
  await self.registration.showNotification(ev ? ev.title : 'Tabbie', {
    body: ev ? ev.body : 'Something finished. Open Tabbie to see it.',
    tag: ev ? 'hx-' + ev.rid : 'hx-push',
    icon: ICON,
    badge: ICON,
    silent: watching,
    data: { url: BASE + String(ev ? ev.url : '').replace(/^\//, '') },
  });
})()));

self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || BASE;
  e.waitUntil((async () => {
    const wins = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    const win = wins.find((c) => c.url.startsWith(BASE));
    if (win) {
      try { await win.focus(); } catch (_) {}
      try { return await win.navigate(url); } catch (_) { return null; }
    }
    return self.clients.openWindow(url);
  })());
});
