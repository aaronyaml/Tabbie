/* Hermes — Apple skin runtime (plain JS, no build step). Visual only:
 *  1. "Route: …" first lines in assistant replies -> tiny badge (.hx-route).
 *  2. Keeps <meta name="theme-color"> on the skin's chrome colours, so the iOS
 *     status bar / PWA chrome matches (upstream boot.js writes its own navy/cream).
 *  3. viewport-fit=cover, so env(safe-area-inset-*) is real on iPhone (home indicator).
 *  4. Tool activity: settled "Processed" groups read "Used N tools" and start
 *     collapsed (once per group per page load, so a user's expand sticks).
 *  5. Phone composer: the workspace name inside the mobile config pill.
 *  6. Empty-state title: wraps "help" in .hx-wing-word for the gradient.
 *  7. Composer focus glow: three blurred copies of the wing ring (.hx-neon, styled in apple-skin.css).
 */
(() => {
  'use strict';
  if (window.__hxAppleSkin) return;
  window.__hxAppleSkin = true;

  // ── 1. Route badges ──────────────────────────────────────────────────────
  const ROUTE = /^\s*Route:\s*/;
  function tagRoutes(root) {
    const scope = root && root.querySelectorAll ? root : document;
    scope.querySelectorAll('.msg-body > p:first-child:not(.hx-route)').forEach((p) => {
      if (p.childElementCount > 2) return;
      const t = p.textContent || '';
      if (t.length > 160 || !ROUTE.test(t)) return;
      p.classList.add('hx-route');
      p.setAttribute('title', t.trim());
    });
  }

  // ── 2. theme-color ───────────────────────────────────────────────────────
  const CHROME = { light: '#FFFFFF', dark: '#0E0F13' };
  let syncing = false;
  function syncThemeColor() {
    if (syncing) return;
    syncing = true;
    try {
      const want = document.documentElement.classList.contains('dark') ? CHROME.dark : CHROME.light;
      document.querySelectorAll('meta[name="theme-color"]').forEach((m) => {
        const media = m.getAttribute('media') || '';
        const v = /dark/.test(media) ? CHROME.dark : /light/.test(media) ? CHROME.light : want;
        if (m.getAttribute('content') !== v) m.setAttribute('content', v);
      });
    } finally { syncing = false; }
  }

  // ── 3. safe areas ────────────────────────────────────────────────────────
  function viewportCover() {
    const m = document.querySelector('meta[name="viewport"]');
    if (m && !/viewport-fit/.test(m.content)) m.setAttribute('content', m.content + ', viewport-fit=cover');
  }

  // ── 4. tool activity ─────────────────────────────────────────────────────
  const autoCollapsed = new Set();
  function quietActivity(root) {
    (root || document).querySelectorAll('.agent-activity-group, .tool-call-group').forEach((g) => {
      const label = g.querySelector(':scope > .tool-call-group-summary .tool-call-group-label');
      const summary = g.querySelector(':scope > .tool-call-group-summary');
      if (!label || !summary) return;
      const settled = (label.getAttribute('data-sweep-label') || label.textContent || '').trim() === 'Processed';
      if (!settled) return;
      let n = g.querySelectorAll('.tool-card-row').length;
      if (!n) { // rows not rendered yet (#5839 lazy worklog): count the stashed row data
        let rows = g._deferredWorklogRows;
        try { if ((!rows || !rows.length) && typeof window._deferredWorklogRowsFromGroup === 'function') rows = window._deferredWorklogRowsFromGroup(g); } catch (_) {}
        if (Array.isArray(rows)) n = rows.filter((r) => r && r.role === 'tool').length;
      }
      const want = n ? `Used ${n} tool${n === 1 ? '' : 's'}` : label.textContent;
      if (label.textContent !== want) label.textContent = want;
      const key = g.getAttribute('data-activity-disclosure-key') || '';
      if (key && !autoCollapsed.has(key)) {
        autoCollapsed.add(key);
        if (!g.classList.contains('tool-call-group-collapsed') && typeof window._toggleActivityGroup === 'function'
            && !g.querySelector('.tool-card.open')) window._toggleActivityGroup(summary);
      }
    });
  }

  // ── 5. workspace pill ────────────────────────────────────────────────────
  function wsPill() {
    const btn = document.getElementById('composerMobileConfigBtn');
    if (!btn) return;
    const src = document.getElementById('composerMobileWorkspaceLabel') || document.querySelector('.composer-workspace-label');
    const name = ((src && src.textContent) || document.querySelector('.composer-workspace-label')?.textContent || '').trim() || 'Workspace';
    let span = btn.querySelector('.hx-ws-label');
    if (!span) { span = document.createElement('span'); span.className = 'hx-ws-label'; btn.appendChild(span); }
    if (span.textContent !== name) span.textContent = name;
    // Phone hides the workspace-files toggle; offer it inside the config sheet instead.
    const panel = document.getElementById('composerMobileConfigPanel');
    const files = document.getElementById('btnWorkspacePanelToggle');
    if (panel && files && !panel.querySelector('.hx-files-action')) {
      const b = document.createElement('button');
      b.type = 'button'; b.className = 'hx-files-action'; b.textContent = 'Files';
      b.setAttribute('aria-label', 'Show workspace files');
      b.addEventListener('click', () => { files.click(); if (typeof window.toggleMobileComposerConfig === 'function' && panel.classList.contains('open')) window.toggleMobileComposerConfig(); });
      panel.insertBefore(b, panel.firstChild.nextSibling);
    }
  }

  // ── 6. greeting gradient ─────────────────────────────────────────────────
  function wingWord() {
    const h = document.querySelector('#emptyState h2');
    if (!h || h.querySelector('.hx-wing-word')) return;
    const t = h.textContent;
    const i = t.toLowerCase().indexOf('help');
    if (i < 0) return;
    const w = document.createElement('span'); w.className = 'hx-wing-word'; w.textContent = t.slice(i, i + 4);
    h.textContent = t.slice(0, i); h.append(w, t.slice(i + 4));
  }

  let queued = false;
  const mo = new MutationObserver(() => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => { queued = false; const m = document.getElementById('messages'); tagRoutes(m); quietActivity(m); wsPill(); wingWord(); syncThemeColor(); });
  });
  function start() {
    viewportCover();
    tagRoutes(document); quietActivity(document); wsPill(); wingWord();
    syncThemeColor();
    const msgs = document.getElementById('messages');
    if (msgs) mo.observe(msgs, { childList: true, subtree: true, characterData: true });
    const box = document.getElementById('composerBox');
    if (box) mo.observe(box, { childList: true, subtree: true, characterData: true });
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
    document.querySelectorAll('meta[name="theme-color"]').forEach((m) => mo.observe(m, { attributes: true, attributeFilter: ['content'] }));
    sidebarScrim();
    sidebarSwipe();
    neon();
    autoReconnect();
  }
  // Live stream dropped (phone locked, Wi-Fi/Tailscale blip, long tool call): upstream finalises
  // the turn with "Connection interrupted" and stops. The run keeps going on the server, so keep
  // retrying with backoff until the server answers, then force-reload the session — loadSession
  // re-attaches to a still-active stream, or shows the settled transcript if it finished.
  function autoReconnect() {
    const ext = window.hermesExt && window.hermesExt.register && window.hermesExt.register('apple-skin');
    if (!ext || !ext.events) return;
    let busy = false;
    const status = (t, ms) => { try { if (typeof setComposerStatus === 'function') setComposerStatus(t, ms); } catch (_) {} };
    const visible = () => document.visibilityState !== 'hidden';
    const waitVisible = () => new Promise((res) => {
      if (visible()) return res();
      const on = () => { if (visible()) { document.removeEventListener('visibilitychange', on); res(); } };
      document.addEventListener('visibilitychange', on);
    });
    async function resume(sid) {
      if (busy || !sid) return;
      busy = true;
      try {
        for (let i = 0, delay = 1000; i < 60; i++, delay = Math.min(delay * 2, 15000)) {
          await waitVisible();
          status(i ? `Reconnecting… (try ${i + 1})` : 'Reconnecting…');
          try {
            const r = await fetch('health', { cache: 'no-store', credentials: 'include' });
            if (r.ok) {
              if (S.session && S.session.session_id === sid && typeof loadSession === 'function') {
                await loadSession(sid, { force: true });
              }
              status('Reconnected', 1500);
              return;
            }
          } catch (_) { /* still offline */ }
          await new Promise((res) => setTimeout(res, delay));
        }
        status('Still offline. Pull to refresh when you’re back.');
      } finally { busy = false; }
    }
    ext.events.on('turn:error', (d) => { if (d && d.status === 'connection_lost') resume(d.sessionId); });
    // Upstream sends that event once per stream. Once a resume re-attaches the same stream, a second
    // drop (common on phones: lock, app switch, Wi-Fi to mobile data) sends nothing. So also watch for
    // the interruption notice itself and resume whenever it is on screen. Each notice is retried at
    // most every 20 s, so a notice the server kept (turn really failed) can't cause a reload loop.
    const tried = new Map(); // session id -> when its notice was last resumed
    const interrupted = () => {
      const m = Array.isArray(S && S.messages) ? S.messages : [];
      const last = m[m.length - 1];
      return !!(last && last.role === 'assistant' && typeof last.content === 'string'
        && last.content.startsWith('**Connection interrupted:**'));
    };
    const kick = (force) => {
      if (busy || !S.session || !visible() || !interrupted()) return;
      const sid = S.session.session_id, at = tried.get(sid) || 0;
      if (!force && Date.now() - at < 20000) return;
      tried.set(sid, Date.now());
      resume(sid);
    };
    addEventListener('online', () => kick(true));
    document.addEventListener('visibilitychange', () => kick(true));
    addEventListener('pageshow', () => kick(true));
    setInterval(() => kick(false), 3000);
  }
  // Phone: drag right anywhere on the page to pull the drawer open, drag left to push
  // it closed. The drawer follows the finger and settles by distance or flick speed.
  // Skips chat rows (they have their own swipe actions), text fields, horizontal scrollers (suggestion strip, code blocks), the
  // Safari back-swipe edge, and the PWA edge zone upstream already handles.
  function sidebarSwipe() {
    const sb = document.querySelector('.sidebar'), ov = document.getElementById('mobileOverlay');
    if (!sb || !ov) return;
    const phone = () => innerWidth <= 640;
    const isOpen = () => sb.classList.contains('mobile-open');
    const standalone = () => document.documentElement.classList.contains('pwa-standalone') || matchMedia('(display-mode: standalone)').matches;
    const hScroller = (el) => {
      for (; el && el !== document.body; el = el.parentElement) {
        const ox = getComputedStyle(el).overflowX;
        if ((ox === 'auto' || ox === 'scroll') && el.scrollWidth > el.clientWidth + 2) return true;
      }
      return false;
    };
    let g = null;
    addEventListener('touchstart', (e) => {
      if (!phone() || e.touches.length !== 1) return;
      const t = e.touches[0], el = e.target;
      if (el.closest('input, textarea, select, [contenteditable="true"], .app-dialog-overlay, .session-item') || hScroller(el)) return;
      const open = isOpen();
      if (!open && t.clientX < (standalone() ? 80 : 24)) return;
      g = { x: t.clientX, y: t.clientY, open, w: sb.getBoundingClientRect().width || innerWidth * 0.86, claimed: false, dx: 0, t: performance.now(), lx: t.clientX, lt: performance.now(), v: 0 };
    }, { passive: true });
    addEventListener('touchmove', (e) => {
      if (!g) return;
      const t = e.touches[0], dx = t.clientX - g.x, dy = t.clientY - g.y;
      if (!g.claimed) {
        if (Math.abs(dy) > 12 && Math.abs(dy) > Math.abs(dx)) { g = null; return; }
        if (Math.abs(dx) < 10 || (g.open ? dx > 0 : dx < 0)) return;
        g.claimed = true;
        sb.style.transition = 'none'; sb.style.visibility = 'visible';
        ov.style.transition = 'none'; ov.style.display = 'block';
      }
      e.preventDefault();
      const now = performance.now();
      g.v = (t.clientX - g.lx) / Math.max(1, now - g.lt); g.lx = t.clientX; g.lt = now;
      const pos = Math.max(-g.w, Math.min(0, (g.open ? 0 : -g.w) + dx)); // drawer offset, -w..0
      g.dx = dx;
      sb.style.transform = `translateX(${pos}px)`;
      ov.style.opacity = String(1 + pos / g.w);
    }, { passive: false });
    const end = () => {
      if (!g) return;
      const s = g; g = null;
      if (!s.claimed) return;
      const progress = Math.abs(s.dx) / s.w, flick = Math.abs(s.v) > 0.45;
      const toggle = progress > 0.33 || flick;
      // Hand back to CSS from the current offset so the settle animates.
      sb.style.transition = ''; ov.style.transition = 'opacity .25s ease';
      void sb.offsetWidth;
      sb.style.transform = ''; sb.style.visibility = '';
      const willOpen = s.open ? !toggle : toggle;
      if (willOpen !== isOpen()) {
        if (willOpen) toggleMobileSidebar(); else closeMobileSidebar();
      }
      ov.style.opacity = willOpen ? '1' : '0';
      setTimeout(() => { ov.style.opacity = ''; ov.style.display = ''; ov.style.transition = ''; }, 260);
    };
    // Capture phase: session rows stop touchend bubbling, which would strand a drag.
    addEventListener('touchend', end, { capture: true, passive: true });
    addEventListener('touchcancel', end, { capture: true, passive: true });
  }
  // Phone: the drawer can open without #mobileOverlay getting .visible, so a tap
  // beside it fell through to the page. Mirror the drawer state onto the overlay;
  // the overlay's own onclick (dismissMobileSidebar) closes it.
  function sidebarScrim() {
    const sb = document.querySelector('.sidebar'), ov = document.getElementById('mobileOverlay');
    if (!sb || !ov) return;
    const sync = () => ov.classList.toggle('visible', sb.classList.contains('mobile-open') && innerWidth <= 640);
    new MutationObserver(sync).observe(sb, { attributes: true, attributeFilter: ['class'] });
    addEventListener('resize', sync);
    sync();
  }
  // The focus glow is the ring's own light falling off: tight, medium and wide blurs of the same
  // gradient, so line and bloom read as one object. Pseudo-elements only give one blur, hence spans.
  // Every composer gets one, whenever it appears: the box can be drawn after this script starts (slow
  // load, sign-in redirect) or replaced later, and a box without it shows no glow at all.
  function neon() {
    const add = (box) => {
      if (box.querySelector(':scope > .hx-neon')) return;
      const n = document.createElement('span');
      n.className = 'hx-neon'; n.setAttribute('aria-hidden', 'true');
      n.innerHTML = '<i></i><i></i><i></i>';
      box.prepend(n);
    };
    const all = () => document.querySelectorAll('.composer-box').forEach(add);
    all();
    addEventListener('focusin', (e) => { const b = e.target.closest && e.target.closest('.composer-box'); if (b) add(b); });
    setInterval(all, 1500);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
