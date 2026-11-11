/* Tabbie — Motion URLs, first thing in <head> (plain JS, no build step).
 * Caddy serves this file in place of static/pwa-startup.js (it is the first script the app shell
 * runs, right after upstream's inline <base href> script); it then loads the untouched upstream
 * file (static/pwa-startup.js?orig=1), which Caddy passes through to the WebUI.
 *
 *   /motion           the studio home     (Caddy serves the app shell for "/")
 *   /motion/<sid>     that film           (Caddy serves the app shell for "/session/<sid>")
 *
 * Here, before any upstream script runs:
 *  - <base href> goes back to the site root (upstream derives it from "/session/", so on /motion/…
 *    every relative path, the service worker and the manifest would land under /motion/);
 *  - the chat is hidden until the studio paints (html.hx-mo-boot), so a refresh never flashes it;
 *  - an old /session/<sid> link to a known motion session becomes /motion/<sid>;
 *  - history.pushState/replaceState are wrapped: while the studio is open it owns the address bar
 *    (window.__hxMoRoute), and a /session/<sid> URL for a motion session is written as /motion/<sid>.
 * motion-studio.js keeps __hxMoRoute in step with what the studio shows.
 *
 * Page routes (/kanban, /settings, /projects, …; Caddy serves the app shell for them):
 *  - on load, /<panel> becomes upstream's own "?panel=<panel>" launch intent (Projects: opened by
 *    routes.js), and window.__hxPanelRoute holds "/<panel>" so the session restore can't take the bar;
 *  - while a page other than Chat is showing, a /session/<sid> or "/" URL is written as that page's
 *    route instead. routes.js keeps __hxPanelRoute in step with the page on screen.
 */
(function () {
  'use strict';
  var L = location, H = history, D = document;
  var MO = /^\/motion(?:\/([0-9a-fA-F]{6,64}))?\/?$/;
  var SESS = /\/session\/([^/?#]+)\/?$/;
  var STORE = 'hx-motion-sessions';
  function known(sid) {
    try { return JSON.parse(localStorage.getItem(STORE) || '[]').indexOf(String(sid)) >= 0; } catch (_) { return false; }
  }
  function dec(s) { try { return decodeURIComponent(s); } catch (_) { return s; } }

  var push = H.pushState, rep = H.replaceState;
  window.__hxMoHist = {
    push: function (st, url) { push.call(H, st, '', url); },
    replace: function (st, url) { rep.call(H, st, '', url); }
  };
  window.__hxMoRoute = null;
  window.__hxMoQuietUntil = 0;
  var PANEL = /^\/(tasks|kanban|skills|memory|workspaces|profiles|todos|insights|logs|settings|projects)\/?$/;
  window.__hxPanelRoute = null;
  window.__hxBootPanel = null;
  var pr = L.pathname.match(PANEL);
  if (pr) {
    window.__hxPanelRoute = '/' + pr[1];
    window.__hxBootPanel = pr[1];
    var q = new URLSearchParams(L.search);
    if (pr[1] !== 'projects') q.set('panel', pr[1]);
    var qs = q.toString();
    rep.call(H, H.state, '', '/' + (qs ? '?' + qs : '') + L.hash);
    var pb = D.querySelector('base');  // "/kanban/" would have made the base "/kanban/"
    if (pb) pb.setAttribute('href', L.origin + '/');
  }

  // The page being opened shows from the first paint (html[data-hx-boot] + declutter.css), not the chat
  // and then the page: Settings, Scheduled… come from /<panel> or upstream's ?panel=<panel>. Cleared once
  // upstream's own switchPanel takes over (declutter.js), or after 8 s at the latest.
  try {
    var bp = window.__hxBootPanel || new URLSearchParams(L.search).get('panel');
    if (bp && bp !== 'chat' && bp !== 'projects' && /^[a-z0-9_-]{1,40}$/.test(bp)) {
      D.documentElement.setAttribute('data-hx-boot', bp);
      // Settings remembers its sub-page in the hash (/settings#appearance): that pane paints first too
      var sec = bp === 'settings' && /^#(appearance|preferences|providers|plugins|extensions|system|help)$/.exec(L.hash);
      if (sec) D.documentElement.setAttribute('data-hx-boot-sec', sec[1]);
      setTimeout(function () { D.documentElement.removeAttribute('data-hx-boot'); D.documentElement.removeAttribute('data-hx-boot-sec'); }, 8000);
    }
  } catch (_) {}

  // Chat list loading state for the sidebar (sidebar.js): which /api/sessions requests are in flight and
  // when the first finished. Wrapped here because upstream asks for the list before extensions load.
  try {
    var SL = window.__hxSessLoad = { pending: 0, since: 0, done: false, failed: false };
    var of = window.fetch;
    if (of) window.fetch = function (u) {
      var url = String((u && u.url) || u || '');
      if (!/\/?api\/sessions(?:\?|$)/.test(url)) return of.apply(this, arguments);
      if (!SL.pending) SL.since = Date.now();
      SL.pending++;
      var fire = function () { try { window.dispatchEvent(new Event('hx-sessload')); } catch (_) {} };
      fire();
      var pr = of.apply(this, arguments);
      pr.then(function (r) { SL.done = SL.done || r.ok; SL.failed = !r.ok; }, function () { SL.failed = true; })
        .then(function () { SL.pending = Math.max(0, SL.pending - 1); fire(); });
      return pr;
    };
  } catch (_) {}

  // Identical reads in flight share one request. On load upstream and the extensions each ask for the same
  // things at once (api/settings four times, api/sessions four times…), and on the one-process server those
  // duplicates queue behind each other: the chat waited ~1 s longer to render. Only plain GETs of read-only
  // endpoints, only while the first is still in flight (nothing is cached), never one with an abort signal.
  try {
    var DEDUPE = /\/?api\/(settings|sessions|projects|profiles|profile\/active|workspaces|models|onboarding\/status|extensions\/status|dashboard\/(config|status)|transcribe\/capability|extensions\/cc-agents\/sidecar\/(executor|hub|inbox|connections|needs|update|agents|projects))(?:\?|$)/;
    var inflight = new Map();
    var df = window.fetch;
    if (df) window.fetch = function (u, init) {
      var req = u && typeof u === 'object' && u.url ? u : null;
      var method = String((init && init.method) || (req && req.method) || 'GET').toUpperCase();
      var url = String(req ? req.url : (u || ''));
      if (method !== 'GET' || req || (init && init.body) || !DEDUPE.test(url)) return df.apply(this, arguments);
      var key;
      try { key = new URL(url, D.baseURI).href; } catch (_) { return df.apply(this, arguments); }
      // a caller's abort signal only cancels its own copy: the shared request runs without one
      var sig = init && init.signal;
      if (sig && sig.aborted) return df.apply(this, arguments);
      var pr = inflight.get(key);
      if (!pr) {
        var own = {};
        if (init) for (var k in init) if (k !== 'signal') own[k] = init[k];
        pr = df.call(this, u, own);
        inflight.set(key, pr);
        var done = function () { if (inflight.get(key) === pr) inflight.delete(key); };
        pr.then(done, done);
      }
      var mine = pr.then(function (r) { return r.clone(); });
      if (!sig) return mine;
      return new Promise(function (ok, no) {
        var off = function () { no(sig.reason || new DOMException('The operation was aborted.', 'AbortError')); };
        sig.addEventListener('abort', off, { once: true });
        mine.then(function (r) { sig.removeEventListener('abort', off); ok(r); }, function (e) { sig.removeEventListener('abort', off); no(e); });
      });
    };
  } catch (_) {}

  // font size (declutter.js): the whole UI scale, set before first paint so nothing jumps after load
  try { var fz = localStorage.getItem('hermes-font-size'); if (/^(small|large|xlarge)$/.test(fz || '')) D.documentElement.setAttribute('data-hx-scale', fz); } catch (_) {}

  // liquid glass (glass.css/js): on unless turned off, set before first paint so nothing flips after load
  try { if (localStorage.getItem('hx-glass') !== 'off') D.documentElement.classList.add('hx-glass'); } catch (_) {}

  var m = L.pathname.match(MO);
  if (!m) {
    var s = L.pathname.match(/^\/session\/([^/?#]+)\/?$/);
    if (s && known(dec(s[1]))) {
      rep.call(H, H.state, '', '/motion/' + s[1] + L.hash);
      m = L.pathname.match(MO);
    }
  }
  if (m) {
    window.__hxMoRoute = m[1] ? '/motion/' + m[1] : '/motion';
    D.documentElement.classList.add('hx-mo-boot');
    setTimeout(function () { D.documentElement.classList.remove('hx-mo-boot'); }, 8000); // never stuck hidden
    var b = D.querySelector('base');
    if (b) b.setAttribute('href', L.origin + '/');
    // the manifest link was resolved against the old base; point it at the root so the PWA keeps one identity
    var mf = D.querySelector('link[rel="manifest"]');
    if (mf) mf.setAttribute('href', L.origin + '/manifest.json');
  }

  function fix(url) {
    if (url == null) return url;
    if (window.__hxMoRoute) return window.__hxMoRoute;
    var u;
    try { u = new URL(String(url), D.baseURI); } catch (_) { return url; }
    // (Projects keeps its #<project>[/<tab>] hash: projects.js puts the open project there)
    if (window.__hxPanelRoute && (u.pathname === '/' || SESS.test(u.pathname))) return window.__hxPanelRoute + (window.__hxPanelRoute === '/projects' || window.__hxPanelRoute === '/settings' ? L.hash : '');
    var k = u.pathname.match(SESS);
    if (k && known(dec(k[1]))) return '/motion/' + k[1] + u.hash;
    return url;
  }
  function here() { return L.pathname + L.search + L.hash; }
  H.pushState = function (st, t, url) {
    if (arguments.length < 3) return push.apply(H, arguments);
    var f = fix(url);
    // the studio already shows this address, or it is settling after Back: don't add an entry
    if (f !== url && (f === here() || f === L.pathname)) return rep.call(H, st, t, f);
    if (Date.now() < window.__hxMoQuietUntil) return rep.call(H, st, t, f);
    return push.call(H, st, t, f);
  };
  H.replaceState = function (st, t, url) {
    if (arguments.length < 3) return rep.apply(H, arguments);
    return rep.call(H, st, t, fix(url));
  };

  // Back/forward: upstream's clearPreview() sets the hidden preview iframes' src to '' on every session
  // load, and each of those blank navigations adds a joint-history entry, so Back steps through
  // invisible iframe history instead of the page's own. Re-blanking an already blank frame is a no-op.
  try {
    var sd = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, 'src');
    if (sd && sd.set && sd.get) {
      Object.defineProperty(HTMLIFrameElement.prototype, 'src', {
        configurable: true, enumerable: sd.enumerable,
        get: function () { return sd.get.call(this); },
        set: function (v) {
          if ((v === '' || v == null) && (this.id === 'previewPdfFrame' || this.id === 'previewHtmlIframe') && !this.getAttribute('src')) return;
          sd.set.call(this, v);
        }
      });
    }
  } catch (_) {}

  // Chat titles, for the skeleton below: the open chat's id and title, newest first, at most 30. Kept
  // only for chats with messages, so "/" restoring an empty scratch chat never shows a chat skeleton.
  var TITLES = 'hx-chat-titles';
  function titles() { try { var a = JSON.parse(localStorage.getItem(TITLES) || '[]'); return Array.isArray(a) ? a : []; } catch (_) { return []; } }
  function titleFor(sid) { var a = titles(); for (var i = 0; i < a.length; i++) if (a[i] && a[i][0] === sid) return String(a[i][1] || ''); return null; }
  (function () {
    var last = '';
    setInterval(function () {
      try {
        var s = S.session;  // upstream's global state (a top-level binding, shared by every classic script)
        if (!s || !s.session_id || !((s.message_count || 0) > 0 || (S.messages && S.messages.length))) return;
        var st = window.hxSessionTitles, t = String((st && st.title ? st.title(s) : s.title) || '').trim().slice(0, 90);
        if (!t || (st && st.isWorker && st.isWorker(s))) return;
        var key = s.session_id + '\n' + t;
        if (key === last) return;
        last = key;
        var a = titles().filter(function (x) { return x && x[0] !== s.session_id; });
        a.unshift([s.session_id, t]);
        localStorage.setItem(TITLES, JSON.stringify(a.slice(0, 30)));
      } catch (_) {}
    }, 1500);
  })();

  // Chat skeleton. Reloading /session/<id> (or "/" when upstream will restore the saved chat) used to
  // show the New chat home for a second or two while the transcript loaded. Instead, html.hx-chat-boot
  // keeps the page laid out as a chat (the home is hidden, the composer sits at the bottom) and a
  // chat-shaped skeleton (reply lines, your bubble on the right) stands where the messages go; the
  // loading cover below draws the same skeleton from the first paint. The real transcript fades in over
  // it. Ends when messages render, when upstream settles on the empty home (empty or missing chat), on
  // New chat, or 8 s after the page loaded whatever happens.
  var CB = null;
  (function () {
    try {
      if (m || window.__hxBootPanel || new URLSearchParams(L.search).get('panel')) return;
      var sm = L.pathname.match(/^\/session\/([^/?#]+)\/?$/), sid = null, title = null;
      if (sm) { sid = dec(sm[1]); title = titleFor(sid); }
      else if (L.pathname === '/' && !L.search) {
        var sv = localStorage.getItem('hermes-webui-session');
        if (sv) { title = titleFor(sv); if (title != null) sid = sv; }
      }
      if (!sid) return;
      CB = { sid: sid, title: title };
    } catch (_) { CB = null; }
    if (!CB) return;
    window.__hxChatBoot = CB;
    var html = D.documentElement, t0 = Date.now();
    html.classList.add('hx-chat-boot');
    var css = D.createElement('style');
    css.id = 'hx-cb-css';
    css.textContent = ''
      // the skeleton itself (also used inside the loading cover)
      + '.hx-csk{display:flex;flex-direction:column;gap:11px;width:100%;max-width:720px;margin:0 auto;padding:0 24px 26px;box-sizing:border-box}'
      + '.hx-csk-l,.hx-csk-av,.hx-csk-u{display:block;border-radius:6px;background:linear-gradient(90deg,rgba(127,127,127,.11) 0%,rgba(127,127,127,.2) 50%,rgba(127,127,127,.11) 100%);'
      + 'background-size:200% 100%;animation:hx-csk-sh 1.5s ease-in-out infinite}'
      + '.hx-csk-l{height:11px;flex:none}'
      + '.hx-csk .hx-csk-who{display:flex;align-items:center;gap:8px;margin:2px 0 3px}'
      + '.hx-csk-av{width:20px;height:20px;border-radius:50%;flex:none}'
      + '.hx-csk-who .hx-csk-l{width:52px;height:10px}'
      + '.hx-csk-u{flex:none;align-self:flex-end;width:min(56%,400px);height:62px;margin:16px 0 18px;border-radius:20px 20px 6px 20px;'
      + 'background:var(--hx-user,color-mix(in srgb,#3054FD 8%,#F4F4F2));animation:none;opacity:.9}'
      + 'html.dark .hx-csk-u{background:var(--hx-user,color-mix(in srgb,#3054FD 18%,#1D1F25))}'
      + '@keyframes hx-csk-sh{from{background-position:100% 0}to{background-position:-100% 0}}'
      + '@media (max-width:640px){.hx-csk{padding:0 12px 22px}.hx-csk-u{width:72%}}'
      + '@media (prefers-reduced-motion:reduce){.hx-csk-l,.hx-csk-av{animation:none}}'
      // inside the app: over the empty transcript, above the composer
      + '#hx-cskel{position:absolute;left:0;right:0;bottom:0;z-index:2;pointer-events:none;transition:opacity .15s ease}'
      + '#hx-cskel.hx-out{opacity:0}'
      + 'html.hx-chat-boot #emptyState,html.hx-chat-boot #hxHome{display:none!important}'
      + 'html.hx-chat-boot #msgInner{opacity:0}'
      + 'html.hx-cb-fade #msgInner{transition:opacity .15s ease}'
      + 'html:not(.hx-chat-boot):not(.hx-cb-fade) #hx-cskel{display:none}';
    html.appendChild(css);

    var W = [[88, 95, 64], [92, 78]];
    CB.html = function () {
      var line = function (w) { return '<span class="hx-csk-l" style="width:' + w + '%"></span>'; };
      var who = '<div class="hx-csk-who"><span class="hx-csk-av"></span><span class="hx-csk-l"></span></div>';
      return '<div class="hx-csk" aria-hidden="true">' + who + W[0].map(line).join('') + '<span class="hx-csk-u"></span>' + who + W[1].map(line).join('') + '</div>';
    };

    var sk = null, done = false;
    function S_() { try { return S; } catch (_) { return null; } }
    function content(inner) {
      for (var c = inner && inner.firstElementChild; c; c = c.nextElementSibling) {
        if (c.classList.contains('hx-skel') || /^\s*Loading conversation/.test(c.textContent || '')) continue;
        return true;
      }
      return false;
    }
    function finish(why) {
      if (done) return;
      done = true;
      clearInterval(iv);
      CB.done = why;
      var st = S_(), es = D.getElementById('emptyState'), inner = D.getElementById('msgInner');
      // timed out with nothing to show and no chat open: give the home back rather than a blank page
      if (why === 'timeout' && es && es.style.display === 'none' && !content(inner) && !(st && st.session)) es.style.display = '';
      html.classList.add('hx-cb-fade');
      html.classList.remove('hx-chat-boot');
      if (sk) sk.classList.add('hx-out');
      setTimeout(function () {
        html.classList.remove('hx-cb-fade');
        if (sk) sk.remove();
        (function rm() { if (D.getElementById('hx-splash')) return setTimeout(rm, 200); css.remove(); })(); // the cover uses it too
      }, 220);
    }
    CB.finish = finish;
    function tick() {
      if (done) return;
      var es = D.getElementById('emptyState'), inner = D.getElementById('msgInner'), st = S_();
      // never stuck: 8 s after the page has loaded (slow phones load the shell slowly too), 20 s at most
      if (Date.now() - t0 > 20000 || (loaded && Date.now() - loaded > 8000)) return finish('timeout');
      if (content(inner)) return finish('messages');
      if (es) {
        if (st && st._bootReady) { if (es.style.display !== 'none') return finish('empty'); }
        // until upstream decides, lay the page out as a chat (home.css / apple-skin.css key off this style)
        else if (es.style.display !== 'none') es.style.display = 'none';
      }
      var shell = D.querySelector('#mainChat .messages-shell');
      if (shell && !sk) {
        sk = D.createElement('div');
        sk.id = 'hx-cskel';
        sk.setAttribute('aria-busy', 'true');
        sk.setAttribute('aria-label', 'Loading chat');
        sk.innerHTML = CB.html();
        shell.appendChild(sk);
      }
      // sit just above the composer (on phones it floats over the bottom of the transcript area)
      var cw = sk && D.getElementById('composerWrap');
      if (cw) {
        var lift = Math.max(0, Math.round(shell.getBoundingClientRect().bottom - cw.getBoundingClientRect().top));
        if (sk.style.bottom !== lift + 'px') sk.style.bottom = lift + 'px';
      }
      // phones show the chat title in the title bar: put the remembered one there until upstream's arrives
      var tt = CB.title && D.getElementById('appTitlebarTitle');
      if (tt && !(st && st.session) && tt.textContent !== CB.title) tt.textContent = CB.title;
    }
    var loaded = 0;
    window.addEventListener('load', function () { loaded = Date.now(); });
    var iv = setInterval(tick, 60);
    // and the moment upstream puts the first message in, not up to 60 ms later
    (function watch() {
      var inner = D.getElementById('msgInner');
      if (!inner) { if (!done) setTimeout(watch, 30); return; }
      var mo = new MutationObserver(function () { if (done) return mo.disconnect(); tick(); if (done) mo.disconnect(); });
      mo.observe(inner, { childList: true });
    })();
    // New chat (button or ⌘K) while it loads: stop at once
    D.addEventListener('click', function (e) {
      if (!done && e.target && e.target.closest && e.target.closest('#btnNewChat, #btnTitlebarNewChat')) finish('new');
    }, true);
    D.addEventListener('keydown', function (e) {
      if (!done && (e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) finish('new');
    }, true);
  })();

  // Loading screen: the app shell takes a couple of seconds to boot (upstream's big scripts, then
  // ~40 extensions, then the session), and without this you'd watch an empty frame, upstream's
  // "Hermes" title and the rail icons shuffling. It covers the page from the first paint and fades
  // out once the sidebar or the chat has content (and the Motion studio has painted, on /motion),
  // or after 10 s whatever happens. The mascot only fades in after 0.3 s, so a quick load never flashes it.
  (function () {
    var html = D.documentElement, t0 = Date.now(), loadAt = 0;
    var css = D.createElement('style');
    css.id = 'hx-splash-css';
    css.textContent = '#hx-splash{position:fixed;inset:0;z-index:2147483646;display:flex;flex-direction:column;align-items:center;'
      + 'justify-content:center;gap:22px;background:#FFFFFF;transition:opacity .25s ease}'
      + 'html.dark #hx-splash{background:#0E0F13}'
      + '#hx-splash>img,#hx-splash>i{opacity:0;animation:hx-sp-in .35s ease .3s forwards}'
      + '#hx-splash>img{width:84px;height:84px;object-fit:contain}'
      + '#hx-splash>i{display:block;width:96px;height:3px;border-radius:3px;background:rgba(128,128,128,.18);overflow:hidden}'
      + '#hx-splash>i:after{content:"";display:block;width:40%;height:100%;border-radius:3px;background:#3054FD;'
      + 'animation:hx-sp-bar 1.1s cubic-bezier(.45,0,.55,1) infinite}'
      + 'html.dark #hx-splash>i:after{background:#6B86FF}'
      + '#hx-splash.hx-out{opacity:0;pointer-events:none}'
      + '@keyframes hx-sp-in{to{opacity:1}}@keyframes hx-sp-bar{from{transform:translateX(-100%)}to{transform:translateX(250%)}}'
      + '@media (prefers-reduced-motion:reduce){#hx-splash>i:after{animation:none;width:100%;opacity:.5}}'
      // reloading a chat: the cover is that chat's skeleton (sidebar, title, messages, composer), not the logo
      + '#hx-splash.hx-cb{flex-direction:row;align-items:stretch;justify-content:flex-start;gap:0}'
      + '.hx-cb-side{flex:none;width:300px;background:#F6F6F4;padding:22px 18px;box-sizing:border-box;display:flex;flex-direction:column;gap:20px}'
      + 'html.dark .hx-cb-side{background:#14161B}'
      + '.hx-cb-side .hx-csk{padding:0;gap:22px;max-width:none}'
      + '.hx-cb-side .hx-csk-l{height:10px}'
      + '.hx-cb-brand{font:700 17px/1 -apple-system,BlinkMacSystemFont,system-ui,sans-serif;color:#141413}'
      + '.hx-cb-act{margin:6px -10px;padding:10px;border-radius:10px;background:rgba(127,127,127,.12);font:500 14px/1.2 -apple-system,BlinkMacSystemFont,system-ui,sans-serif;'
      + 'color:#141413;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}'
      + 'html.dark .hx-cb-brand,html.dark .hx-cb-act,html.dark .hx-cb-top{color:#F5F5F7}'
      + '.hx-cb-main{flex:1;min-width:0;display:flex;flex-direction:column}'
      + '.hx-cb-gap{flex:1}.hx-cb-main>.hx-csk{flex:none}'
      + '.hx-cb-top{display:none;flex:none;box-sizing:content-box;height:46px;align-items:center;justify-content:center;'
      + 'font:600 16px/1 -apple-system,BlinkMacSystemFont,system-ui,sans-serif;color:#141413;border-bottom:.5px solid rgba(127,127,127,.18)}'
      + '.hx-cb-top{position:relative}.hx-cb-ic{position:absolute;bottom:12px;width:22px;height:22px;fill:none;stroke:currentColor;stroke-width:1.6;stroke-linecap:round}'
      + '.hx-cb-top span{max-width:62%;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}'
      + '.hx-cb-comp{flex:none;width:calc(100% - 20px);max-width:720px;height:94px;margin:10px auto 14px;box-sizing:border-box;border-radius:24px;'
      + 'background:#F1F1EF;border:1px solid rgba(127,127,127,.18)}'
      + 'html.dark .hx-cb-comp{background:#1D1F25}'
      + '@media (display-mode:standalone){.hx-cb-top{padding-top:env(safe-area-inset-top)}}'
      + '@media (max-width:640px){.hx-cb-top{display:flex}.hx-cb-comp{height:104px;margin:8px auto calc(6px + env(safe-area-inset-bottom))}}';
    var el = D.createElement('div');
    el.id = 'hx-splash';
    el.setAttribute('aria-label', 'Loading Tabbie');
    el.setAttribute('role', 'progressbar');
    if (CB) {
      var esc = function (t) { return String(t).replace(/[&<>"']/g, function (c) { return '&#' + c.charCodeAt(0) + ';'; }); };
      var side = false;
      try { side = innerWidth > 640 && localStorage.getItem('hermes-webui-sidebar-collapsed') !== '1'; } catch (_) {}
      el.className = 'hx-cb';
      el.innerHTML = (side ? '<div class="hx-cb-side"><div class="hx-cb-brand">Tabbie</div>'
          + '<div class="hx-csk"><span class="hx-csk-l" style="width:46%"></span><span class="hx-csk-l" style="width:38%"></span><span class="hx-csk-l" style="width:44%"></span><span class="hx-csk-l" style="width:40%"></span></div>'
          + (CB.title ? '<div class="hx-cb-act">' + esc(CB.title) + '</div>' : '')
          + '<div class="hx-csk"><span class="hx-csk-l" style="width:78%"></span><span class="hx-csk-l" style="width:64%"></span><span class="hx-csk-l" style="width:84%"></span><span class="hx-csk-l" style="width:58%"></span><span class="hx-csk-l" style="width:70%"></span></div></div>' : '')
        + '<div class="hx-cb-main"><div class="hx-cb-top"><svg class="hx-cb-ic" style="left:18px" viewBox="0 0 24 24"><path d="M4 6h16M4 12h16M4 18h16"/></svg><span>' + esc(CB.title || '')
        + '</span><svg class="hx-cb-ic" style="right:18px" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg></div><div class="hx-cb-gap"></div>'
        + CB.html() + '<div class="hx-cb-comp"></div></div>';
    } else {
      el.innerHTML = '<img src="/hx-brand/mascot-256.png" alt=""><i></i>';
    }
    html.appendChild(css);
    html.appendChild(el);
    window.addEventListener('load', function () { loadAt = Date.now(); });
    function has(id) { var n = D.getElementById(id); return !!(n && n.children.length); }
    function ready() {
      if (Date.now() - t0 > 10000) return true;
      if (!loadAt || html.classList.contains('hx-mo-boot')) return false;
      return has('sessionList') || has('msgInner') || Date.now() - loadAt > 2500;
    }
    var iv = setInterval(function () {
      if (!ready()) return;
      clearInterval(iv);
      // two frames so whatever just rendered is painted before the cover lifts
      requestAnimationFrame(function () { requestAnimationFrame(function () {
        el.classList.add('hx-out');
        setTimeout(function () { el.remove(); css.remove(); }, 300);
      }); });
    }, 80);
  })();

  // then the untouched upstream script, from the root
  var v = '';
  try { v = new URL(D.currentScript.src).searchParams.get('v') || ''; } catch (_) {}
  D.write('<script src="' + L.origin + '/static/pwa-startup.js?orig=1' + (v ? '&v=' + encodeURIComponent(v) : '') + '"><\/script>');
})();
