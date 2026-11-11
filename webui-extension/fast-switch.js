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
 *
 * Lighter transcripts (needs webui-patches/0001 on the server; an unpatched server ignores the
 * extra query params, so this is a no-op there):
 *   - every transcript request asks for hx_strip_images=1: pasted screenshots stay out of the JSON
 *     (the transcript shows them from their attachment path; one chat carried 13 MB of base64);
 *   - a fresh open (the prefetch above) also asks for msg_bytes=96 KB: the server shrinks the
 *     30-message tail until it fits, so the newest messages paint first; once they're on screen the
 *     rest of the 30 is filled in the background through upstream's own _loadOlderMessages().
 */
(function () {
  'use strict';
  const TTL_PAIR = 10000;  // transcript started alongside the metadata request
  const TTL_EARLY = 6000;  // boot + hover/touch prefetches
  const FIRST_PAINT_BYTES = 96 * 1024;
  const FILL_TO = 30; // visible rows the background fill tops a capped first page up to
  const cache = new Map(); // exact api path -> { p, exp }
  const stats = window.__hxFastSwitchStats = { prefetch: 0, hit: 0, stale: 0, miss: 0, fill: 0 };

  // Upstream globals (top-level let/const/function in classic scripts: reachable by name, not on window).
  const G = {
    S: () => (typeof S !== 'undefined' ? S : undefined),
    _allSessions: () => (typeof _allSessions !== 'undefined' ? _allSessions : undefined),
    _msgLimitMax: () => (typeof _msgLimitMax !== 'undefined' ? _msgLimitMax : undefined),
    _messageReloadLimitForSession: () => (typeof _messageReloadLimitForSession !== 'undefined' ? _messageReloadLimitForSession : undefined),
    _resolveSessionIdFromSidebarLineage: () => (typeof _resolveSessionIdFromSidebarLineage !== 'undefined' ? _resolveSessionIdFromSidebarLineage : undefined),
    _sessionIdFromLocation: () => (typeof _sessionIdFromLocation !== 'undefined' ? _sessionIdFromLocation : undefined),
    _loadOlderMessages: () => (typeof _loadOlderMessages !== 'undefined' ? _loadOlderMessages : undefined),
    _loadingSessionId: () => (typeof _loadingSessionId !== 'undefined' ? _loadingSessionId : undefined),
    _messagesTruncated: () => (typeof _messagesTruncated !== 'undefined' ? _messagesTruncated : undefined),
    _scrollPinned: () => (typeof _scrollPinned !== 'undefined' ? _scrollPinned : undefined),
    scrollToBottom: () => (typeof scrollToBottom !== 'undefined' ? scrollToBottom : undefined),
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
  // Transcript request -> its lighter form. `capFirstPaint` only for a fresh tail (no msg_before).
  function lite(path, capFirstPaint) {
    if (!/^\/api\/session\?/.test(path) || !/[?&]messages=1(&|$)/.test(path)) return path;
    let out = path;
    if (!/[?&]hx_strip_images=/.test(out)) out += '&hx_strip_images=1';
    if (capFirstPaint && /[?&]msg_limit=\d/.test(out) && !/[?&]msg_before=/.test(out) && !/[?&]msg_bytes=/.test(out)) {
      out += `&msg_bytes=${FIRST_PAINT_BYTES}`;
    }
    return out;
  }
  const visibleRows = (msgs) => (Array.isArray(msgs) ? msgs.filter((m) => m && (m.role === 'user' || m.role === 'assistant')).length : 0);
  // After a capped first page is on screen, top it up to FILL_TO rows in the background.
  function scheduleFill(sid, v) {
    const s = v && v.session;
    if (!s || !s._messages_truncated || visibleRows(s.messages) >= FILL_TO) return;
    let tries = 0;
    const idle = window.requestIdleCallback || ((fn) => setTimeout(fn, 200));
    const step = () => {
      if (currentSid() !== sid || ++tries > 40) return; // switched away, or never settled (~8 s)
      const load = g('_loadOlderMessages');
      const S = g('S');
      if (g('_loadingSessionId') || typeof load !== 'function' || !S || !Array.isArray(S.messages) || !S.messages.length) {
        setTimeout(step, 200);
        return;
      }
      if (!g('_messagesTruncated') || visibleRows(S.messages) >= FILL_TO) return;
      stats.fill++;
      // Upstream's loader assumes the reader scrolled up to trigger it and unpins the view. This fill
      // isn't user-driven: if the view was pinned to the bottom and the reader didn't scroll meanwhile,
      // pin it again so late-sizing media can't leave it short of the newest message.
      const wasPinned = g('_scrollPinned') === true;
      let touched = false;
      const mark = () => { touched = true; };
      const evs = ['wheel', 'touchmove', 'keydown', 'pointerdown'];
      evs.forEach((t) => window.addEventListener(t, mark, { passive: true, capture: true }));
      Promise.resolve().then(load).catch(() => {}).then(() => {
        evs.forEach((t) => window.removeEventListener(t, mark, { capture: true }));
        const toBottom = g('scrollToBottom');
        if (wasPinned && !touched && currentSid() === sid && typeof toBottom === 'function') {
          try { toBottom(); } catch (_) {}
        }
      });
    };
    setTimeout(() => idle(step, { timeout: 1500 }), 150);
  }
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
    const capped = lite(path, true);
    const p = Promise.resolve().then(() => orig(capped, { timeoutMs: 120000, redirect401: false, timeoutToast: false }));
    p.catch(() => {});
    cache.set(path, { p, exp: Date.now() + ttl, capped: capped.includes('msg_bytes=') });
    stats.prefetch++;
  }
  function take(path) {
    const hit = cache.get(path);
    if (!hit) return null;
    cache.delete(path);
    return hit.exp > Date.now() ? hit : null;
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
      const self = this, args = Array.prototype.slice.call(arguments);
      args[0] = lite(path, false);
      const fresh = () => orig.apply(self, args);
      const hit = take(path);
      if (!hit) stats.miss++;
      const use = (v) => {
        stats.hit++;
        if (hit.capped) scheduleFill(sid, v);
        return v;
      };
      const out = hit ? hit.p.then((v) => (usable(v, sid) ? use(v) : (stats.stale++, fresh())), () => (stats.stale++, fresh())) : fresh();
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
