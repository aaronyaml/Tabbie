/* Tabbie — Motion: a sidebar entry right under Chat that opens the motion studio (plain JS, no build step).
 * Pairs with motion.css; the studio itself (guided flow + editor + Zoetie chat) is motion-studio.js.
 * Zoetie (named after the zoetrope, the first animation toy) is the motion buddy: Tabbie's palette,
 * its own character and idle clips (idle-buddy.js plays them).
 *
 * This file owns what is shared with the chat:
 *  - the Motion entries in the rail (desktop) and the drawer list (phones), right under Chat;
 *  - motion sessions: each studio film has its own chat session (workspace = the studio folder, title
 *    "Motion · <film>"). They are studio-only: never listed in the chat sidebar or its search
 *    (rows are filtered before upstream paints them, and any row that slips through is hidden),
 *    and landing on one by URL opens the studio instead of the raw transcript.
 */
(function () {
  'use strict';
  if (window.__hxMotion) return;
  window.__hxMotion = true;

  const NAME = 'Zoetie';
  const LOC = window.HX_LOCAL || {};
  let HOME = LOC.motion || `${LOC.hermes || `${LOC.home || ''}/hermes`}/motion`; // motion-studio.js sets the sidecar's answer
  const MARK = 'Motion · ';
  const STORE = 'hx-motion-sessions';
  const icon = (n, s) => (typeof window.hxIcon === 'function' ? window.hxIcon(n, s) : '');
  const sess = () => { try { return S.session || null; } catch (_) { return null; } };
  const sidNow = () => { const s = sess(); return (s && s.session_id) || null; };

  // ── motion sessions: remembered here, plus anything in the studio folder titled "Motion · …" ──
  let mine;
  try { mine = new Set(JSON.parse(localStorage.getItem(STORE) || '[]')); } catch (_) { mine = new Set(); }
  function remember(sid) {
    if (!sid || mine.has(String(sid))) return;
    mine.add(String(sid));
    try { localStorage.setItem(STORE, JSON.stringify([...mine].slice(-300))); } catch (_) {}
  }
  const marked = (s) => !!s && s.workspace === HOME && String(s.title || s.display_title || '').startsWith(MARK);
  function isRow(s) {
    if (!s) return false;
    if (mine.has(String(s.session_id))) return true;
    if (marked(s)) { remember(s.session_id); return true; }
    return false;
  }
  const isMotion = (sid) => !!sid && (mine.has(String(sid)) || (sid === sidNow() && isRow(sess())));
  // The command-centre router's own Claude Code transcripts for studio turns ("COMMAND CENTRE REQUEST …
  // Route: Claude — motion studio (/motion)") are plumbing too: hidden wherever sessions are listed.
  const plumbing = new Set();
  function hides(s) {
    if (!s) return false;
    if (isRow(s)) return true;
    const t = String(s.title || s.display_title || '');
    if (/^COMMAND CENTRE REQUEST\b/.test(t) && /motion studio/i.test(t)) { plumbing.add(String(s.session_id)); return true; }
    return plumbing.has(String(s.session_id));
  }
  window.hxMotion = { isMotion, remember, hides, NAME, MARK, setHome: (p) => { HOME = p; } };

  // ── Motion runs on Claude Code only: hidden (html.hx-no-motion) unless Claude is enabled and signed in.
  // The last answer is remembered so the entry doesn't flash in on load; re-checked on focus and every 5 min.
  const OFF_KEY = 'hx-motion-off';
  function setOff(off) {
    const root = document.documentElement;
    if (root.classList.contains('hx-no-motion') === off) return;
    root.classList.toggle('hx-no-motion', off);
    try { if (off) localStorage.setItem(OFF_KEY, '1'); else localStorage.removeItem(OFF_KEY); } catch (_) {}
    window.dispatchEvent(new CustomEvent('hx-motion-ready', { detail: { ready: !off } }));
  }
  try { if (localStorage.getItem(OFF_KEY)) document.documentElement.classList.add('hx-no-motion'); } catch (_) {}
  let readyAt = 0;
  async function checkReady(force) {
    if (!force && Date.now() - readyAt < 60000) return;
    readyAt = Date.now();
    try {
      const r = await fetch('api/extensions/cc-agents/sidecar/agents', { credentials: 'same-origin', cache: 'no-store' });
      if (!r.ok) return;  // sidecar down: keep the last answer
      const d = await r.json();
      setOff(!((d.enabled || []).includes('claude') && d.linked && d.linked.claude));
    } catch (_) {}
  }
  window.hxMotion.ready = () => !document.documentElement.classList.contains('hx-no-motion');
  window.hxMotion.check = () => checkReady(true);
  checkReady(true);
  setInterval(() => checkReady(true), 300000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) checkReady(false); });

  // ── keep them out of the chat sidebar (desktop list, phone drawer, search) ──
  function wrapRows() {
    const orig = window._sessionRowsWithActiveEphemeralSession;
    if (typeof orig !== 'function') return false;
    if (orig.__hxMotion) return true;
    const w = function () { const rows = orig.apply(this, arguments); return Array.isArray(rows) ? rows.filter((s) => !hides(s)) : rows; };
    w.__hxMotion = true;
    window._sessionRowsWithActiveEphemeralSession = w;
    try { if (typeof renderSessionListFromCache === 'function') renderSessionListFromCache(); } catch (_) {}
    return true;
  }
  function hideRows() {
    document.querySelectorAll('.session-item').forEach((el) => {
      const t = el.querySelector('[data-sid]');
      const hide = !!(t && (isMotion(t.dataset.sid) || plumbing.has(t.dataset.sid)));
      if (el.classList.contains('hx-mo-row') !== hide) el.classList.toggle('hx-mo-row', hide);
    });
  }
  // A fresh device has nothing remembered: learn the marker from the server list once.
  (async () => {
    try {
      const r = await fetch('api/sessions', { credentials: 'same-origin' });
      const d = await r.json();
      (d.sessions || []).forEach(hides);
      hideRows();
      try { if (typeof renderSessionListFromCache === 'function') renderSessionListFromCache(); } catch (_) {}
    } catch (_) {}
  })();

  // ── sidebar entries (rail on desktop, drawer list on phones), right under Chat ──
  function navButtons() {
    const rail = document.querySelector('.rail .rail-btn.nav-tab[data-panel="chat"]');
    if (rail && !document.getElementById('hxMotionRail')) {
      const b = document.createElement('button');
      b.id = 'hxMotionRail';
      b.type = 'button';
      b.className = 'rail-btn nav-tab has-tooltip hx-mo-nav';
      b.dataset.tooltip = 'Motion';
      b.setAttribute('aria-label', 'Motion');
      b.innerHTML = icon('Video01Icon', 20);
      rail.after(b);
    }
    const side = document.querySelector('.sidebar-nav .nav-tab[data-panel="chat"]');
    if (side && !document.getElementById('hxMotionTab')) {
      const b = document.createElement('button');
      b.id = 'hxMotionTab';
      b.type = 'button';
      b.className = 'nav-tab has-tooltip has-tooltip--bottom hx-mo-nav';
      b.dataset.label = 'Motion';
      b.dataset.tooltip = 'Motion';
      b.setAttribute('aria-label', 'Motion');
      b.innerHTML = icon('Video01Icon', 18);
      side.after(b);
    }
  }

  function sync() {
    const root = document.documentElement;
    const studio = root.classList.contains('hx-studio-open');
    const sid = sidNow();
    // (a motion chat without a film opens as a plain chat: motion-studio.js openBySid)
    const mo = !!sid && isMotion(sid) && !(window.hxStudio && window.hxStudio.noFilm && window.hxStudio.noFilm(sid));
    if (sid && isRow(sess())) remember(sid);
    // A motion session is never shown as a transcript: the studio is the only way in.
    const leaving = window.hxStudio && window.hxStudio.leaving && window.hxStudio.leaving();
    root.classList.toggle('hx-mo-raw', mo && !studio);
    if (mo && !studio && !leaving && window.hxStudio) window.hxStudio.open();
    root.classList.toggle('hx-mo', studio || mo);
    navButtons();
    hideRows();
    document.querySelectorAll('.hx-mo-nav').forEach((b) => {
      b.classList.toggle('hx-mo-on', studio);
      b.setAttribute('aria-current', studio ? 'page' : 'false');
    });
  }
  window.hxMotion.sync = sync;

  // ── wiring ──
  document.addEventListener('click', (e) => {
    const t = e.target;
    if (!t || !t.closest) return;
    if (t.closest('.hx-mo-nav')) {
      e.preventDefault();
      try { if (typeof closeMobileSidebar === 'function') closeMobileSidebar(); } catch (_) {}
      if (window.hxStudio) window.hxStudio.open({ home: true, push: true }); // the rail always lands on the film list
      return;
    }
    // Any other primary nav entry, "+" or a session leaves the studio.
    if (t.closest('.rail .nav-tab, .sidebar-nav .nav-tab, #btnNewChat, #btnTitlebarNewChat, .session-item, .hx-rec')) {
      if (window.hxStudio && window.hxStudio.isOpen()) {
        window.hxStudio.close({ keepChat: !!t.closest('.session-item, #btnNewChat, #btnTitlebarNewChat') });
        // The studio sits over the panel the WebUI still thinks is open, so upstream would read a
        // click on that tab as "same tab again" and collapse the sidebar. Closing the studio is the switch.
        if (window.hxSameTab && window.hxSameTab(t)) { e.preventDefault(); e.stopPropagation(); }
      }
    }
  }, true);

  let queued = false;
  const later = () => { if (!queued) { queued = true; requestAnimationFrame(() => { queued = false; sync(); }); } };
  function start() {
    if (!wrapRows()) { const iv = setInterval(() => { if (wrapRows()) clearInterval(iv); }, 300); setTimeout(() => clearInterval(iv), 30000); }
    sync();
    // (the studio's own DOM churns while it plays; it calls sync() itself)
    new MutationObserver((recs) => { if (recs.some((r) => !(r.target.closest && r.target.closest('#hxStudio')))) later(); })
      .observe(document.body, { childList: true, subtree: true });
    setInterval(sync, 700); // session switches that don't touch the DOM we watch
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
