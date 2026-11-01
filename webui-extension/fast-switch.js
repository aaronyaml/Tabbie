/* Hermes — faster session open/switch (plain JS, no build step). Speed only; same requests, same data.
 *
 * Upstream's loadSession() fetches the session in two serial round trips: metadata
 * (/api/session?…&messages=0) and only after that returns, the transcript (…&messages=1). This
 * wraps window.api() so the transcript request starts together with the metadata one, and also
 * starts both early:
 *   - boot: as soon as this script runs (upstream boot is still waiting on /api/settings), for the
 *     session the URL / saved-session points at;
 *   - sidebar: on hover (desktop) or touchstart (phone), before the click lands.
 * The prefetched promise is handed to the exact same URL request upstream makes later, once, within
 * a short TTL. It is discarded (and the request made normally) when:
 *   - the session is live (streaming / pending user message) — those depend on exact timing;
 *   - the sidebar list, which upstream keeps fresh from its events stream, shows a newer
 *     message_count / updated_at than the prefetched payload;
 *   - any non-GET api() call happened in between (could have changed the session);
 *   - the prefetch failed or got a 401 (the real call then redirects / errors as before).
 */
(function () {
  'use strict';
  const TTL_PAIR = 10000;  // transcript started alongside the metadata request
  const TTL_EARLY = 6000;  // boot + hover/touch prefetches
  const cache = new Map(); // exact api path -> { p, exp }
  const stats = window.__hxFastSwitchStats = { prefetch: 0, hit: 0, stale: 0, miss: 0 };

  // Upstream globals (top-level let/const/function in classic scripts: reachable by name, not on window).
  const G = {
    S: () => (typeof S !== 'undefined' ? S : undefined),
    _allSessions: () => (typeof _allSessions !== 'undefined' ? _allSessions : undefined),
    _msgLimitMax: () => (typeof _msgLimitMax !== 'undefined' ? _msgLimitMax : undefined),
    _messageReloadLimitForSession: () => (typeof _messageReloadLimitForSession !== 'undefined' ? _messageReloadLimitForSession : undefined),
    _resolveSessionIdFromSidebarLineage: () => (typeof _resolveSessionIdFromSidebarLineage !== 'undefined' ? _resolveSessionIdFromSidebarLineage : undefined),
    _sessionIdFromLocation: () => (typeof _sessionIdFromLocation !== 'undefined' ? _sessionIdFromLocation : undefined),
  };
  const g = (name) => { try { return G[name](); } catch (_) { return undefined; } };
  const metaPath = (sid) => `/api/session?session_id=${encodeURIComponent(sid)}&messages=0&resolve_model=0`;
  function msgsPath(sid) {
    // Mirrors _ensureMessagesLoaded(); any mismatch just means no cache hit.
    const limitFn = g('_messageReloadLimitForSession');
    if (typeof limitFn !== 'function') return null;
    let lim;
    try { lim = limitFn(sid); } catch (_) { return null; }
    const max = g('_msgLimitMax');
    const b = lim && (typeof max !== 'number' || lim <= max) ? lim : null;
    return `/api/session?session_id=${encodeURIComponent(sid)}&messages=1&resolve_model=0`
      + (b ? `&msg_limit=${b}&expand_renderable=1` : '');
  }
  const META_RX = /^\/api\/session\?session_id=([^&]+)&messages=0&resolve_model=0$/;
  const currentSid = () => { const S = g('S'); return S && S.session ? S.session.session_id : null; };
  function listEntry(sid) {
    const all = g('_allSessions');
    return Array.isArray(all) ? all.find((s) => s && s.session_id === sid) : null;
  }
  const isLive = (s) => !!(s && (s.active_stream_id || s.is_streaming || s.has_pending_user_message || s.pending_user_message));
  function usable(v, sid) {
    const s = v && v.session;
    if (!s || s.session_id !== sid || isLive(s)) return false;
    const e = listEntry(sid);
    if (e) {
      if (isLive(e)) return false;
      if (Number(e.message_count || 0) !== Number(s.message_count || 0)) return false;
      if (Number(e.updated_at || 0) > Number(s.updated_at || 0) + 0.001) return false;
    }
    return true;
  }

  let orig = null;
  function prefetch(path, ttl) {
    if (!path || !orig) return;
    const hit = cache.get(path);
    if (hit && hit.exp > Date.now()) return;
    const p = Promise.resolve().then(() => orig(path, { timeoutMs: 120000, redirect401: false, timeoutToast: false }));
    p.catch(() => {});
    cache.set(path, { p, exp: Date.now() + ttl });
    stats.prefetch++;
  }
  function take(path) {
    const hit = cache.get(path);
    if (!hit) return null;
    cache.delete(path);
    return hit.exp > Date.now() ? hit.p : null;
  }
  function prefetchSession(sid, ttl) {
    if (!sid || sid === currentSid() || isLive(listEntry(sid))) return;
    prefetch(metaPath(sid), ttl);
    prefetch(msgsPath(sid), ttl);
  }

  function wrap() {
    if (typeof window.api !== 'function' || window.api.__hxFastSwitch) return false;
    orig = window.api;
    const wrapped = function (path, opts) {
      if (typeof path !== 'string') return orig.apply(this, arguments);
      const method = String((opts && opts.method) || 'GET').toUpperCase();
      if (method !== 'GET') { cache.clear(); return orig.apply(this, arguments); }
      if (!path.startsWith('/api/session?')) return orig.apply(this, arguments);
      const sid = decodeURIComponent((/[?&]session_id=([^&]+)/.exec(path) || [])[1] || '');
      const self = this, args = arguments;
      const fresh = () => orig.apply(self, args);
      const hitP = take(path);
      if (!hitP) stats.miss++;
      const out = hitP ? hitP.then((v) => (usable(v, sid) ? (stats.hit++, v) : (stats.stale++, fresh())), () => (stats.stale++, fresh())) : fresh();
      // Real switch (or boot): start the transcript now instead of after the metadata returns.
      if (META_RX.test(path) && sid && sid !== currentSid()) prefetch(msgsPath(sid), TTL_PAIR);
      return out;
    };
    wrapped.__hxFastSwitch = true;
    window.api = wrapped;
    return true;
  }

  // Sidebar rows (upstream) and the home screen's recent list.
  const ROW = '.session-item[data-sid], .session-child-session[data-sid], .hx-rec[data-sid]';
  function rowSid(t) {
    const row = t && t.closest ? t.closest(ROW) : null;
    if (!row) return null;
    let sid = row.dataset.sid;
    const resolve = g('_resolveSessionIdFromSidebarLineage');
    if (typeof resolve === 'function') { try { sid = resolve(sid) || sid; } catch (_) {} }
    return sid;
  }
  let hoverTimer = 0;
  document.addEventListener('pointerover', (e) => {
    if (e.pointerType === 'touch') return;
    const sid = rowSid(e.target);
    clearTimeout(hoverTimer);
    if (sid) hoverTimer = setTimeout(() => prefetchSession(sid, TTL_EARLY), 60);
  }, true);
  document.addEventListener('touchstart', (e) => prefetchSession(rowSid(e.target), TTL_EARLY), { passive: true, capture: true });
  document.addEventListener('mousedown', (e) => prefetchSession(rowSid(e.target), TTL_EARLY), true);

  function bootPrefetch() {
    // Boot is still awaiting /api/settings etc.; start the session it is about to restore.
    if (currentSid()) return;
    let sid = null;
    const fromLoc = g('_sessionIdFromLocation');
    try { sid = typeof fromLoc === 'function' ? fromLoc() : null; } catch (_) {}
    if (!sid) { try { sid = localStorage.getItem('hermes-webui-session'); } catch (_) {} }
    if (sid) prefetchSession(sid, TTL_EARLY);
  }

  if (wrap()) bootPrefetch();
  else { const t = setInterval(() => { if (wrap()) clearInterval(t); }, 100); }
})();
