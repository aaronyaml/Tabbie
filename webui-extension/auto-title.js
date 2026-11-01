/* Hermes — name a chat from its first prompt, before any reply (plain JS, no build step).
 * As soon as a chat has its first user message and no manual title, ask the cc-agents sidecar
 * for a 2-6 word title (Claude Haiku through the Claude Code CLI on the user's subscription, ~3 s),
 * then rename it via upstream's /api/session/rename. A rename counts as a manual title, so
 * upstream's own post-reply title generation leaves it alone.
 */
(function () {
  'use strict';
  const TITLE_API = 'api/extensions/cc-agents/sidecar/title?text=';
  const done = new Set();
  const AUTO = /^(untitled|new chat|)$/i;

  function firstPrompt() {
    try {
      const users = (S.messages || []).filter((m) => m && m.role === 'user');
      if (users.length !== 1) return null;
      const c = users[0].content;
      const text = typeof c === 'string' ? c
        : Array.isArray(c) ? c.map((p) => (p && (p.text || (typeof p === 'string' ? p : ''))) || '').join(' ') : '';
      return text.trim() || null;
    } catch (_) { return null; }
  }

  async function run() {
    let s;
    try { s = S.session; } catch (_) { return; }
    if (!s || !s.session_id || done.has(s.session_id) || s.manual_title) return;
    const text = firstPrompt();
    if (!text) return;
    const sid = s.session_id;
    done.add(sid);
    // Upstream may already show the prompt itself as a provisional title; replace that too.
    const before = (s.title || '').trim();
    if (!AUTO.test(before) && !text.startsWith(before.replace(/…$/, ''))) return;
    try {
      // The sidecar answers {pending:true} while Haiku is still working; ask again (same text).
      let title = '';
      for (let i = 0; i < 6 && !title; i++) {
        const r = await fetch(TITLE_API + encodeURIComponent(text.slice(0, 2000)), { credentials: 'same-origin', cache: 'no-store' });
        if (!r.ok) break;
        const d = await r.json();
        title = (d.title || '').trim();
        if (!d.pending) break;
      }
      if (!title) return;
      const body = JSON.stringify({ session_id: sid, title });
      if (typeof window.api === 'function') await window.api('/api/session/rename', { method: 'POST', body });
      else await fetch('api/session/rename', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body });
      try {
        if (S.session && S.session.session_id === sid) { S.session.title = title; S.session.manual_title = true; }
        if (typeof syncTopbar === 'function') syncTopbar();
        if (typeof renderSessionList === 'function') renderSessionList();
      } catch (_) {}
    } catch (_) { done.delete(sid); }
  }

  function start() { setInterval(run, 700); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
