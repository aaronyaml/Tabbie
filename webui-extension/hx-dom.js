/* Tabbie — one shared page watcher. Several extensions re-scan the page when it changes; while a reply
 * streams the page changes on every chunk, so each of them ran up to 60 times a second. Subscribers here
 * share one MutationObserver and run together at most every 150 ms (the first change runs at once).
 *   window.hxDom.on(fn)   fn() after page changes (DOM nodes or text)
 */
(() => {
  'use strict';
  if (window.hxDom) return;
  const subs = [];
  const GAP = 150;
  let last = 0, timer = 0;
  function run() {
    timer = 0;
    last = performance.now();
    for (const fn of subs) { try { fn(); } catch (e) { console.warn('hxDom subscriber', e); } }
  }
  function changed() {
    if (timer) return;
    const wait = Math.max(0, last + GAP - performance.now());
    timer = setTimeout(() => requestAnimationFrame(run), wait);
  }
  const mo = new MutationObserver(changed);
  function start() { mo.observe(document.body, { childList: true, subtree: true, characterData: true }); }
  if (document.body) start(); else document.addEventListener('DOMContentLoaded', start, { once: true });
  window.hxDom = { on(fn) { subs.push(fn); } };
})();
