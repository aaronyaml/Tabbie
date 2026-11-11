/* Hermes — notifications that name the chat and say what's needed (plain JS, no build step).
 * Upstream sends "Response complete" / "Clarification needed" / "Approval required" with no hint of
 * which chat. This wraps upstream's global sendBrowserNotification (messages.js, a top-level function,
 * so messages.js's own bare calls resolve to the wrapper) and rewrites only the title:
 *   "<chat>: needs your answer", "<chat>: plan ready to approve", "<chat>: done", ...
 * The original is always called, so the user's notification setting, permission rules, tags and the
 * click-through URL are untouched. Check it is live with `sendBrowserNotification.__hx`.
 */
(function () {
  'use strict';
  const API = 'api/extensions/cc-agents/sidecar/';
  const MAX = 48;

  function clip(s) {
    s = String(s || '').replace(/\s+/g, ' ').trim();
    return s.length > MAX ? s.slice(0, MAX - 1).trimEnd() + '…' : s;
  }
  function isDefault(t) { return !t || /^(untitled|new chat)$/i.test(t); }
  function chatName(sid) {
    let t = '';
    try {
      const pick = (s) => (typeof _sessionDisplayTitle === 'function' ? _sessionDisplayTitle(s) : (s.display_title || s.title || ''));
      if (sid && S.session && S.session.session_id === sid) t = pick(S.session);
      if (isDefault(t) && sid && typeof _allSessions !== 'undefined' && Array.isArray(_allSessions)) {
        const row = _allSessions.find((s) => s && s.session_id === sid);
        if (row) t = pick(row);
      }
    } catch (_) {}
    if (isDefault(t) && sid) {
      const row = document.querySelector(`.session-item[data-sid="${CSS.escape(sid)}"]`);
      const el = row && (row.querySelector('.session-title'));
      if (el) t = (el.textContent || '').trim().split('\n')[0];
    }
    if (isDefault(t)) {
      try { t = typeof assistantDisplayName === 'function' ? assistantDisplayName() : ''; } catch (_) { t = ''; }
    }
    return clip(t || 'Tabbie');
  }

  // Newest Tabbie request for the chat (1.5 s budget; any failure just means "done").
  async function latestStatus(sid) {
    if (!sid) return '';
    const ctl = 'AbortController' in window ? new AbortController() : null;
    const timer = setTimeout(() => { if (ctl) ctl.abort(); }, 1500);
    try {
      const r = await Promise.race([
        fetch(API + 'session?id=' + encodeURIComponent(sid), { credentials: 'same-origin', cache: 'no-store', signal: ctl ? ctl.signal : undefined }),
        new Promise((_, no) => setTimeout(() => no(new Error('timeout')), 1600)),
      ]);
      if (!r.ok) return '';
      const d = await r.json();
      const reqs = (d && Array.isArray(d.requests)) ? d.requests.slice() : [];
      if (!reqs.length) return '';
      reqs.sort((a, b) => String(a.created || a.id).localeCompare(String(b.created || b.id)));
      return String(reqs[reqs.length - 1].status || '');
    } catch (_) {
      return '';
    } finally {
      clearTimeout(timer);
    }
  }
  const PHRASE = {
    awaiting_answer: 'needs your answer', awaiting_answers: 'needs your answer',
    awaiting_approval: 'plan ready to approve', failed: "didn't finish", claude_limited: 'hit the usage limit', paused: 'paused at the usage limit',
  };

  // Would upstream show anything at all? If not, call straight through without the sidecar round trip.
  function wouldShow(opts) {
    if (opts.force) return true;
    if (!window._notificationsEnabled || !('Notification' in window)) return false;
    if (Notification.permission === 'denied') return false;
    if (opts.forceHidden) return true;
    try { if (typeof _isBackgroundedForBrowserNotification === 'function') return !!_isBackgroundedForBrowserNotification(); } catch (_) {}
    return !!document.hidden;
  }

  function install() {
    const orig = window.sendBrowserNotification;
    if (typeof orig !== 'function') return false;
    if (orig.__hx) return true;
    const wrapped = function (title, body, options) {
      const opts = options || {};
      const sid = opts.sid || null;
      try {
        if (title === 'Clarification needed') return orig.call(this, `${chatName(sid)}: needs your answer`, body, options);
        if (title === 'Approval required') return orig.call(this, `${chatName(sid)}: needs your approval`, body, options);
        if (title !== 'Response complete' || !wouldShow(opts)) return orig.call(this, title, body, options);
      } catch (_) {
        return orig.call(this, title, body, options);
      }
      // The visibility decision is made now, at completion time (as upstream does); the status lookup
      // that follows must not let a user returning within 1.5 s veto it, so forceHidden is carried over.
      const name = chatName(sid);
      const self = this;
      const carry = Object.assign({}, opts, { forceHidden: true });
      return latestStatus(sid).then((st) => orig.call(self, `${name}: ${PHRASE[st] || 'done'}`, body, carry),
        () => orig.call(self, `${name}: done`, body, carry));
    };
    wrapped.__hx = true;
    wrapped.__hxOrig = orig;
    // upstream stores its one-shot permission flag on the function object it calls (now the wrapper)
    if (orig._autoPermissionRequested) wrapped._autoPermissionRequested = true;
    try { window.sendBrowserNotification = wrapped; } catch (_) { return false; }
    return window.sendBrowserNotification === wrapped;
  }

  // Extension scripts can load before upstream's deferred messages.js; retry briefly.
  function start() {
    if (install()) return;
    let n = 0;
    const t = setInterval(() => { if (install() || ++n > 60) clearInterval(t); }, 500);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
