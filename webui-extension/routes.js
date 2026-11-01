/* Tabbie — page routes: every page has its own address, so a refresh lands back on it.
 *   /kanban, /tasks, /skills, /memory, /workspaces, /profiles, /todos, /insights, /logs, /settings
 *   /projects   the Projects overlay        /motion   the studio (motion-boot.js + motion-studio.js)
 *   /session/<id> or /   the chat (upstream)
 * motion-boot.js (first script) turns a page address into the page to open on load; Caddy serves the
 * app shell for these paths. Here: keep the address bar on the page that's showing. Panel switches
 * replace the address (no extra Back entries), so Back/Forward still step through chats.
 */
(() => {
  'use strict';
  if (window.__hxRoutes) return;
  window.__hxRoutes = true;

  const PANEL = /^\/(tasks|kanban|skills|memory|workspaces|profiles|todos|insights|logs|settings|projects)\/?$/;
  const BOOT_GRACE = 10000;  // the launch page may take a moment to show (saved chat restores first)
  const started = Date.now();
  const raw = (url) => (window.__hxMoHist ? window.__hxMoHist.replace(history.state, url) : history.replaceState(history.state, '', url));

  const panelNow = () => { try { return typeof _currentPanel !== 'undefined' && _currentPanel ? _currentPanel : 'chat'; } catch (_) { return 'chat'; } };
  const studioOpen = () => document.documentElement.classList.contains('hx-studio-open');
  const projOpen = () => document.documentElement.classList.contains('hx-proj-open');
  const sid = () => { try { return (typeof S !== 'undefined' && S && S.session && S.session.session_id) || ''; } catch (_) { return ''; } };
  const chatUrl = () => {
    const s = sid();
    if (!s) return '/';
    try { if (typeof _sessionUrlForSid === 'function') return _sessionUrlForSid(s); } catch (_) {}
    return '/session/' + encodeURIComponent(s);
  };

  let booting = window.__hxBootPanel;
  function bootStep() {
    if (!booting) return;
    if (booting === 'projects') {
      if (window.hxProjects && document.body) { window.hxProjects.open(); booting = null; window.__hxBootPanel = null; }
    } else if (panelNow() === booting) {
      booting = null; window.__hxBootPanel = null;
    }
    if (booting && Date.now() - started > BOOT_GRACE) { booting = null; window.__hxBootPanel = null; }
  }

  function sync() {
    bootStep();
    if (booting) return;           // the launch page hasn't shown yet: leave its address alone
    if (studioOpen()) { window.__hxPanelRoute = null; return; }  // the studio owns the bar (/motion…)
    const p = panelNow();
    const want = projOpen() ? '/projects' : (p && p !== 'chat' && PANEL.test('/' + p) ? '/' + p : null);
    if (want) {
      window.__hxPanelRoute = want;
      if (location.pathname !== want) raw(want + location.hash);
      return;
    }
    window.__hxPanelRoute = null;
    if (PANEL.test(location.pathname)) raw(chatUrl());  // back on the chat: its own address again
  }

  // Panel switches and overlay open/close: sync right away; a slow interval catches everything else.
  const later = () => setTimeout(sync, 0);
  document.addEventListener('click', (e) => {
    if (e.target && e.target.closest && e.target.closest('.nav-tab, .rail-btn, .session-item, #btnNewChat, #btnTitlebarNewChat')) setTimeout(sync, 60);
  }, true);
  new MutationObserver(later).observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
  addEventListener('popstate', later);
  setInterval(sync, 500);
  sync();
})();
