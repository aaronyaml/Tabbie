/* Runtime (plain JS, no build step at load). Visual only:
 *  1. li(name, size) -> Hugeicon (stroke 1.5, currentColor), same signature.
 *  2. Inline Lucide <svg>s (index.html + JS templates) are fingerprinted and
 *     swapped in place on load and via MutationObserver; size/class/style kept.
 *  3. Emoji / glyph icons in the UI chrome -> Hugeicon (or dropped). Chat
 *     message text, code, inputs and user-named things are left alone.
 *  window.hxIcon(name, size) renders any bundled Hugeicon by its export name.
 */
(() => {
  'use strict';
  if (window.__hxIcons) return;
  window.__hxIcons = true;

  /*@DATA@*/

  const ROOT = 'viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"';
  const mark = (name) => P[name].replace(/^<(\w+)/, '<$1 data-h=""');
  function hxIcon(name, size = 24) {
    if (!P[name]) return '';
    return `<svg width="${size}" height="${size}" ${ROOT} data-hi="${name}" aria-hidden="true" `
      + `style="display:inline-block;vertical-align:-0.15em;flex-shrink:0">${mark(name)}</svg>`;
  }
  window.hxIcon = hxIcon;

  // ── 1. li() ──────────────────────────────────────────────────────────────
  if (typeof window.li === 'function' && !window.li.__hx) {
    const orig = window.li;
    const hx = function (name, size = 16) { return LI[name] ? hxIcon(LI[name], size) : orig(name, size); };
    hx.__hx = true;
    window.li = hx;
  }

  // ── 2. inline svg swap ───────────────────────────────────────────────────
  const GEO = ['d', 'points', 'x1', 'y1', 'x2', 'y2', 'cx', 'cy', 'r', 'x', 'y', 'width', 'height', 'rx', 'ry'];
  const NOPE = /^(title|defs|lineargradient|radialgradient|stop|text|svg|use|mask|clippath|pattern|image|foreignobject)$/;
  function fingerprint(svg) {
    const out = [];
    for (const el of svg.querySelectorAll('*')) {
      const tag = el.tagName.toLowerCase();
      if (tag === 'g') continue;
      if (NOPE.test(tag)) return null;
      out.push(tag + ':' + GEO.filter((k) => el.hasAttribute(k)).map((k) => el.getAttribute(k).replace(/\s+/g, ' ').trim()).join(','));
    }
    return out.length ? out.join('|') : null;
  }
  function hash(s) { let h = 5381; for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0; return (h >>> 0).toString(36); }
  // Context beats shape: the composer's send arrow is SentIcon.
  const contextName = (svg) => (svg.parentElement && svg.parentElement.id === 'btnSend' ? 'SentIcon' : null);

  function swap(svg) {
    const first = svg.firstElementChild;
    if (first && first.hasAttribute('data-h')) return;
    const f = fingerprint(svg);
    const name = contextName(svg) || (f && FP[hash(f)]);
    if (!name || !P[name]) return;
    svg.setAttribute('viewBox', '0 0 24 24');
    const fill = svg.getAttribute('fill');
    if (!fill || fill === 'none') svg.setAttribute('fill', 'none'); // a filled root (pinned star, stop) stays solid
    svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-width', '1.5');
    svg.setAttribute('stroke-linecap', 'round');
    svg.setAttribute('stroke-linejoin', 'round');
    svg.setAttribute('data-hi', name);
    svg.innerHTML = mark(name);
  }

  // ── 3. emoji / glyphs in chrome ──────────────────────────────────────────
  const RX = /(?:\p{Extended_Pictographic}|[★☐✓✕↻▲▴▸▼▾✦])(?:️|‍\p{Extended_Pictographic}️?)*/gu;
  const KEEP = new Set(['©', '®', '™', '‼', '⁉', '↔', '↕', '↖', '↘', '↙', '↪', '⌘', '⌨', '⏎', '▪', '▫', '◀', '●']);
  const CONTENT = '.msg-body, .msg-text, pre, code, textarea, input, select, [contenteditable=""], [contenteditable="true"], '
    + '.session-title, .session-item-title, .kanban-card-title, .kanban-board-switcher-item-icon, .xterm, .cm-editor, .memory-content, .hx-glyph';
  const CHROME_OK = '.msg-media-link, .pdf-preview-load, .html-preview-load, .pdf-preview-header, .task-done, .task-todo, .attach-chip-media, .tree-collapsible';
  const base = (g) => g.replace(/[️‍].*$/u, '');
  const isContent = (el) => !!el && !!el.closest(CONTENT) && !el.closest(CHROME_OK);

  function scrubText(node) {
    const t = node.data;
    RX.lastIndex = 0;
    if (!RX.test(t)) return;
    const parent = node.parentElement;
    if (!parent || isContent(parent) || parent.closest('svg, script, style, title')) return;
    RX.lastIndex = 0;
    const frag = document.createDocumentFragment();
    let last = 0, changed = false, m;
    while ((m = RX.exec(t))) {
      const g = base(m[0]);
      if (KEEP.has(g)) continue;
      changed = true;
      frag.append(t.slice(last, m.index));
      const name = GLYPH[g];
      let end = m.index + m[0].length;
      if (name) {
        const s = document.createElement('span');
        s.className = 'hx-glyph';
        s.setAttribute('aria-hidden', 'true');
        s.innerHTML = hxIcon(name, 16);
        frag.append(s);
      } else if (t[end] === ' ') end++; // dropped emoji takes its trailing space
      last = end;
    }
    if (!changed) return;
    frag.append(t.slice(last));
    node.replaceWith(frag);
  }
  const ATTRS = ['placeholder', 'title', 'data-tooltip', 'aria-label'];
  function scrubAttrs(el) {
    if (isContent(el) && !el.matches('input, textarea')) return;
    for (const a of ATTRS) {
      const v = el.getAttribute(a);
      if (!v) continue;
      RX.lastIndex = 0;
      if (!RX.test(v)) continue;
      const nv = v.replace(RX, (g) => (KEEP.has(base(g)) ? g : '')).replace(/\s{2,}/g, ' ').trim();
      if (nv !== v) el.setAttribute(a, nv);
    }
  }

  function process(root) {
    if (!root) return;
    if (root.nodeType === 3) { scrubText(root); return; }
    if (root.nodeType !== 1) return;
    const own = root.closest('svg');
    if (own) { swap(own); return; }
    root.querySelectorAll('svg').forEach(swap);
    scrubAttrs(root);
    root.querySelectorAll('[placeholder], [title], [data-tooltip], [aria-label]').forEach(scrubAttrs);
    if (isContent(root) && !root.querySelector(CHROME_OK)) return;
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const hits = [];
    for (let n = w.nextNode(); n; n = w.nextNode()) { RX.lastIndex = 0; if (RX.test(n.data)) hits.push(n); }
    hits.forEach(scrubText);
  }

  function start() {
    process(document.body);
    new MutationObserver((muts) => {
      for (const m of muts) {
        if (m.type === 'childList') {
          if (m.target.closest && m.target.closest('svg')) process(m.target);
          m.addedNodes.forEach(process);
        } else if (m.type === 'characterData') scrubText(m.target);
        else scrubAttrs(m.target);
      }
    }).observe(document.body, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
