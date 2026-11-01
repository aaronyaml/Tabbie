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

  // then the untouched upstream script, from the root
  var v = '';
  try { v = new URL(D.currentScript.src).searchParams.get('v') || ''; } catch (_) {}
  D.write('<script src="' + L.origin + '/static/pwa-startup.js?orig=1' + (v ? '&v=' + encodeURIComponent(v) : '') + '"><\/script>');
})();
