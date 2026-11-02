/* Tabbie — extension overflow loader. Upstream injects at most 32 extension scripts (and 32 stylesheets)
 * and silently drops the rest, so extensions near the end of extensions.json never ran. This sits first in
 * the manifest and loads, in manifest order, any enabled extension's files the page didn't get.
 */
(() => {
  'use strict';
  if (window.__hxOverflow) return;
  window.__hxOverflow = true;

  const BASE = '/extensions/';
  const path = (u) => { try { return new URL(u, location.href).pathname; } catch (_) { return ''; } };

  Promise.all([
    fetch(BASE + 'extensions.json', { cache: 'no-cache' }).then((r) => r.json()),
    fetch('/api/extensions/status', { credentials: 'same-origin' }).then((r) => r.json()),
  ]).then(([manifest, status]) => {
    // what upstream injects itself (it adds them one by one, so the DOM can't tell us yet)
    const on = new Map((status.extensions || []).map((e) => [e.id, e.effective_enabled !== false]));
    const scripts = new Set((status.script_urls || []).map(path));
    const sheets = new Set((status.stylesheet_urls || []).map(path));
    if (!status.enabled) return;
    for (const ext of manifest.extensions || []) {
      if (ext.enabled === false || on.get(ext.id) === false) continue;
      for (const f of ext.stylesheets || []) {
        if (sheets.has(path(BASE + f))) continue;
        const l = document.createElement('link');
        l.rel = 'stylesheet';
        l.href = BASE + f;
        document.head.appendChild(l);
      }
      for (const f of ext.scripts || []) {
        if (scripts.has(path(BASE + f))) continue;
        const s = document.createElement('script');
        s.src = BASE + f;
        s.async = false;  // run in manifest order
        document.body.appendChild(s);
      }
    }
  }).catch((e) => console.warn('overflow-loader', e));
})();
