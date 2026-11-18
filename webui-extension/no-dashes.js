/* Tabbie: no em dashes anywhere on screen (plain JS, no build step). Visual only.
 * Model replies, upstream labels, our own labels, placeholders, tooltips and the tab title: every long dash is
 * rewritten as the punctuation a person would use. "**Label** — text" reads "Label: text", "a — b" and "a—b" read
 * "a, b", "3—5" reads "3-5", and a lone dash (an empty table cell) becomes "-". Code, inputs and editable text
 * are left exactly as they are. The route line keeps its shape for the parsers, with " · " as its separator.
 */
(() => {
  'use strict';
  if (window.__hxNoDashes) return;
  window.__hxNoDashes = true;

  const DASH = '—';
  const SKIP = 'title, pre, code, kbd, samp, textarea, input, script, style, [contenteditable=""], [contenteditable="true"], .katex, .mermaid, svg';
  const LABEL = /^(STRONG|B)$/;

  function undash(t, lead, first) {
    if (lead) t = t.replace(/^\s*—\s*/, ': ');
    t = t.replace(/(\d)\s*—\s*(\d)/g, '$1-$2');
    if (/^\s*—\s*$/.test(t)) return t.replace(DASH, '-');
    t = t.replace(/\s*—\s*/g, ', ').replace(/,\s*([,.;:!?)\]])/g, '$1');
    if (first) t = t.replace(/^,\s*/, '');
    return t;
  }
  window.hxUndash = (t) => (String(t).includes(DASH) ? undash(String(t), false, true) : t);

  function fixText(n) {
    if (!n.data.includes(DASH)) return;
    const p = n.parentElement;
    if (!p || p.closest(SKIP)) return;
    let v;
    if (p.closest('.hx-route')) v = n.data.replace(/\s*—\s*/g, ' · ');
    else {
      const prev = n.previousSibling;
      v = undash(n.data, !!(prev && prev.nodeType === 1 && LABEL.test(prev.tagName)), !prev);
    }
    if (v !== n.data) n.data = v;
  }

  function fixAttrs(el) {
    for (const a of ['placeholder', 'title', 'aria-label', 'data-sweep-label']) {
      const v = el.getAttribute && el.getAttribute(a);
      if (v && v.includes(DASH) && !el.closest('pre, code')) el.setAttribute(a, undash(v, false, true));
    }
  }

  function sweep(root) {
    if (!root) return;
    if (root.nodeType === 3) { fixText(root); return; }
    if (root.nodeType !== 1 && root.nodeType !== 9) return;
    if (root.nodeType === 1) fixAttrs(root);
    if (!(root.textContent || '').includes(DASH) && !(root.querySelector && root.querySelector('[title*="—"], [placeholder*="—"], [aria-label*="—"]'))) return;
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, { acceptNode: (t) => (t.data.includes(DASH) ? 1 : 3) });
    for (let t = w.nextNode(); t; t = w.nextNode()) fixText(t);
    if (root.querySelectorAll) root.querySelectorAll('[title*="—"], [placeholder*="—"], [aria-label*="—"], [data-sweep-label*="—"]').forEach(fixAttrs);
  }

  function fixTitle() {
    if (document.title.includes(DASH)) document.title = document.title.replace(/\s*—\s*/g, ' · ');
  }

  const dirty = new Set();
  let queued = false;
  function flush() {
    queued = false;
    const roots = [...dirty];
    dirty.clear();
    roots.forEach((r) => { if (r.isConnected) sweep(r); });
    fixTitle();
  }
  const mo = new MutationObserver((recs) => {
    for (const r of recs) {
      if (r.type === 'childList') r.addedNodes.forEach((n) => dirty.add(n));
      else dirty.add(r.target);
    }
    if (!queued && dirty.size) { queued = true; requestAnimationFrame(flush); }
  });

  function start() {
    sweep(document.body);
    fixTitle();
    mo.observe(document.documentElement, { childList: true, subtree: true, characterData: true,
      attributes: true, attributeFilter: ['placeholder', 'title', 'aria-label', 'data-sweep-label'] });
  }
  if (document.body) start(); else document.addEventListener('DOMContentLoaded', start, { once: true });
})();
