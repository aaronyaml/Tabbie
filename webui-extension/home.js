/* Tabbie — new-chat home (plain JS, no build step). Pairs with home.css.
 * A small time-of-day greeting, the composer, and one short list: chats that need you (a question or
 * a plan to approve), chats still working, then the latest few. It fades while you type.
 */
(function () {
  'use strict';
  const NAME = (window.HX_LOCAL || {}).name || '';  // your first name, from local.js
  const icon = (n, s) => (typeof window.hxIcon === 'function' ? window.hxIcon(n, s) : '');
  const esc = (t) => String(t).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const phone = () => matchMedia('(max-width: 640px)').matches;

  function greeting() {
    const h = new Date().getHours();
    const part = h < 5 ? 'Up late' : h < 12 ? 'Morning' : h < 18 ? 'Afternoon' : 'Evening';
    return NAME ? `${part}, ${NAME}` : part;
  }
  function setGreeting() {
    const h2 = document.querySelector('#emptyState h2');
    if (!h2) return;
    const want = greeting();
    if (h2.textContent !== want) {
      const [part, name] = want.split(', ');
      h2.innerHTML = `${esc(part)}, <span class="hx-name">${esc(name)}</span>`;
    }
  }

  const ago = (sec) => {
    const d = Date.now() / 1000 - sec;
    if (d < 3600) return `${Math.max(1, Math.round(d / 60))}m ago`;
    if (d < 86400) return `${Math.round(d / 3600)}h ago`;
    if (d < 172800) return 'yesterday';
    return `${Math.round(d / 86400)}d ago`;
  };
  // Hub worker transcripts ("COMMAND CENTRE REQUEST …") are plumbing; pasted-text envelopes get readable
  // titles. session-titles.js owns the rules; the fallback keeps workers out if it isn't loaded.
  const ST = () => window.hxSessionTitles || null;
  const isWorker = (s) => (ST() ? ST().isWorker(s) : /^(COMMAND CENTRE REQUEST\b|You are (the execution worker of the user's command centre|taking over from Claude Code|the PLANNER in a personal AI command centre))/.test(String(s.title || '').trim()));
  const titleOf = (s) => (ST() ? ST().title(s) : String(s.title || ''));
  const hidden = (s) => isWorker(s) || !!(window.hxMotion && window.hxMotion.hides && window.hxMotion.hides(s)); // Motion studio sessions live in the studio
  let recent = [], recentAt = 0;
  async function loadRecent() {
    if (Date.now() - recentAt < 30000) return;
    recentAt = Date.now();
    try {
      const r = await fetch('api/sessions', { credentials: 'same-origin' });
      const d = await r.json();
      const list = (d.sessions || d || []).filter((s) => s && s.title && (s.message_count || 0) > 0 && !s.archived && !hidden(s));
      list.sort((a, b) => (b.updated_at || b.last_message_at || 0) - (a.updated_at || a.last_message_at || 0));
      recent = list.slice(0, 60);
      render();
    } catch (_) {}
  }

  // Chats whose newest request asked a question or has a plan to approve (sidecar /inbox).
  let waiting = [], waitingAt = 0;
  async function loadInbox() {
    if (Date.now() - waitingAt < 30000) return;
    waitingAt = Date.now();
    try {
      const r = await fetch('api/extensions/cc-agents/sidecar/inbox', { credentials: 'same-origin' });
      if (!r.ok) return;
      waiting = ((await r.json()).items || []).slice(0, 3);
      render();
    } catch (_) {}
  }

  let home = null;
  // One short list under the composer: chats waiting on you, then what's running, then the latest few.
  // No suggestion cards or project buttons: the composer's project chip already starts a chat anywhere.
  const MAX = 5;
  function render() {
    if (!home) return;
    const seen = new Set();
    const rows = [];
    waiting.forEach((w) => {
      if (seen.has(w.session)) return;
      seen.add(w.session);
      rows.push({ sid: w.session, title: w.title, tag: w.kind === 'plan' ? 'Plan to approve' : 'Question', tagCls: 'is-need', sub: w.text });
    });
    const live = (s) => !!(s.active_stream_id || s.is_streaming || s.has_pending_user_message);
    // the sidebar's project filter applies here too (All: everything; Unassigned: chats without one)
    let pf = null;
    try { pf = typeof _activeProject !== 'undefined' ? _activeProject : null; } catch (_) {}
    const inProj = (s) => !pf || (pf === '__none__' ? !s.project_id : s.project_id === pf);
    recent.filter((s) => !hidden(s) && !seen.has(s.session_id) && inProj(s))
      .sort((x, y) => (live(y) ? 1 : 0) - (live(x) ? 1 : 0))
      .forEach((s) => {
        if (rows.length >= MAX) return;
        seen.add(s.session_id);
        rows.push({ sid: s.session_id, title: titleOf(s), tag: live(s) ? 'Working' : '', tagCls: 'is-live', when: ago(s.updated_at || s.last_message_at || 0) });
      });
    const html = rows.length ? `<div class="hx-list" role="list">${rows.slice(0, MAX).map((r) => `
      <button type="button" class="hx-row" role="listitem" data-sid="${esc(r.sid)}">
        <span class="hx-row-tt">${esc(r.title || 'Untitled chat')}</span>
        ${r.tag ? `<span class="hx-row-tag ${r.tagCls}">${esc(r.tag)}</span>` : ''}
        <span class="hx-row-meta">${esc(r.sub || r.when || '')}</span>
      </button>`).join('')}</div>` : '';
    if (home.dataset.html !== html) { home.innerHTML = html; home.dataset.html = html; }
  }

  function place() {
    const wrap = document.getElementById('composerWrap');
    if (!wrap) return;
    if (!home) {
      home = document.createElement('section');
      home.id = 'hxHome';
      home.setAttribute('aria-label', 'Suggestions');
      home.addEventListener('click', (e) => {
        const r = e.target.closest('.hx-row');
        if (r && typeof loadSession === 'function') loadSession(r.dataset.sid);
      });
    }
    // Under the composer on desktop, just above it on phones.
    if (phone()) { if (home.nextElementSibling !== wrap) wrap.before(home); }
    else if (wrap.nextElementSibling !== home) wrap.after(home);
    render();
  }

  function syncTyping() {
    const ta = document.getElementById('msg');
    document.documentElement.classList.toggle('hx-typing', !!(ta && ta.value.trim()));
  }

  function tick() { setGreeting(); place(); syncTyping(); }
  function start() {
    tick(); loadRecent(); loadInbox();
    addEventListener('input', (e) => { if (e.target && e.target.id === 'msg') syncTyping(); }, true);
    addEventListener('resize', place);
    const es = document.getElementById('emptyState');
    if (es) new MutationObserver(() => { tick(); loadRecent(); loadInbox(); }).observe(es, { attributes: true, childList: true, subtree: true, characterData: true });
    setInterval(tick, 1500); // composer cleared by sending, session switches, i18n re-renders
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
