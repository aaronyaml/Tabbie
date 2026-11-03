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
    if (window.__hxPanelRoute && (u.pathname === '/' || SESS.test(u.pathname))) return window.__hxPanelRoute + (window.__hxPanelRoute === '/projects' ? L.hash : '');
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
      + '#hx-splash img,#hx-splash i{opacity:0;animation:hx-sp-in .35s ease .3s forwards}'
      + '#hx-splash img{width:84px;height:84px;object-fit:contain}'
      + '#hx-splash i{display:block;width:96px;height:3px;border-radius:3px;background:rgba(128,128,128,.18);overflow:hidden}'
      + '#hx-splash i:after{content:"";display:block;width:40%;height:100%;border-radius:3px;background:#3054FD;'
      + 'animation:hx-sp-bar 1.1s cubic-bezier(.45,0,.55,1) infinite}'
      + 'html.dark #hx-splash i:after{background:#6B86FF}'
      + '#hx-splash.hx-out{opacity:0;pointer-events:none}'
      + '@keyframes hx-sp-in{to{opacity:1}}@keyframes hx-sp-bar{from{transform:translateX(-100%)}to{transform:translateX(250%)}}'
      + '@media (prefers-reduced-motion:reduce){#hx-splash i:after{animation:none;width:100%;opacity:.5}}';
    var el = D.createElement('div');
    el.id = 'hx-splash';
    el.setAttribute('aria-label', 'Loading Tabbie');
    el.setAttribute('role', 'progressbar');
    el.innerHTML = '<img src="/hx-brand/mascot-256.png" alt=""><i></i>';
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
