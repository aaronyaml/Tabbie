/* Tabbie — one dropdown behaviour for the whole app, taken from the composer model picker (model-picker.js).
 * Pairs with dropdowns.css (the look: .hx-pop surface, rows, headers, search, sheet, animations).
 *
 * window.hxPop
 *   .safeTop()                      top safe-area inset in px (Dynamic Island / status bar)
 *   .phone()                        true at <=640px: popovers become bottom sheets
 *   .place(el, anchor, opts)        desktop: fixed popover above the anchor, or below it when a full list
 *                                   wouldn't fit above and there's more room below; capped to the room.
 *                                   opts {width: px | 'anchor', minWidth, maxWidth, side}. Writes CSS vars
 *                                   (--hxp-left/top/bottom/w/max) read by .hx-pop-fixed, so other scripts
 *                                   writing top/left/max-height inline can't move it. Returns {below, room}.
 *   .adopt(el, opts)                for a dropdown someone else opens and closes (upstream toggles a class):
 *                                   while open it gets .hx-pop, moves to <body>, is placed as above (phone:
 *                                   bottom sheet + scrim), Escape / outside press / scrim close it, arrow keys
 *                                   move between rows. opts {anchor(): el, isOpen(el), close(), width,
 *                                   rows: selector, sheet: true, onOpen(), onClose(), outside: true}
 *   .watch(selector, opts)          for menus created and removed by other code (e.g. sidebar's
 *                                   .hx-more-menu): each one gets .hx-pop, arrow-key focus moves, and on
 *                                   phones a scrim (a press on it reaches the owner's outside handler).
 *                                   opts {rows: selector, sheet: true}
 *   .close(el, done)                plays the close animation, then calls done() (e.g. el.remove()).
 * Classes other code can target: .hx-pop (every popover/sheet), .hx-pop.is-sheet, .hx-pop-scrim.
 */
(() => {
  'use strict';
  if (window.hxPop) return;

  const phone = () => window.matchMedia('(max-width: 640px)').matches;
  let probe = null;
  function safeTop() {
    if (!probe) {
      probe = document.createElement('div');
      probe.style.cssText = 'position:fixed;top:0;left:0;width:0;visibility:hidden;pointer-events:none;height:env(safe-area-inset-top,0px)';
      document.body.appendChild(probe);
    }
    return probe.offsetHeight || 0;
  }

  function place(el, anchor, opts) {
    const o = opts || {};
    const r = anchor.getBoundingClientRect();
    const vw = document.documentElement.clientWidth || window.innerWidth;
    const vh = window.innerHeight;
    let w = o.width === 'anchor' ? r.width : (o.width || 320);
    w = Math.max(o.minWidth || 0, Math.min(w, o.maxWidth || 9999, vw - 24));
    const left = Math.max(12, Math.min(r.left, vw - w - 12));
    const side = o.side || (r.top - 8 < 472 && vh - r.bottom > r.top ? 'below' : 'above');
    const below = side === 'below';
    const room = Math.max(160, below ? vh - r.bottom - 20 : r.top - 20);
    el.classList.add('hx-pop-fixed');
    el.classList.toggle('is-below', below);
    const s = el.style;
    s.setProperty('--hxp-left', left + 'px');
    s.setProperty('--hxp-w', w + 'px');
    s.setProperty('--hxp-top', below ? (r.bottom + 8) + 'px' : 'auto');
    s.setProperty('--hxp-bottom', below ? 'auto' : Math.max(12, vh - r.top + 8) + 'px');
    s.setProperty('--hxp-max', Math.min(o.maxHeight || 560, room) + 'px');
    return { below, room, side };
  }

  // ── scrim + sheet ───────────────────────────────────────────────────────
  function makeScrim(onPress) {
    const s = document.createElement('div');
    s.className = 'hx-pop-scrim';
    s.addEventListener('click', (e) => { e.stopPropagation(); if (onPress) onPress(); });
    document.body.appendChild(s);
    requestAnimationFrame(() => s.classList.add('is-on'));
    return s;
  }
  function sheet(el, on) {
    el.classList.toggle('is-sheet', on);
    if (on) el.style.setProperty('--hxp-max', Math.max(240, window.innerHeight - safeTop() - 16) + 'px');
  }

  // ── keyboard: arrows move focus between rows ────────────────────────────
  function rowsOf(el, sel) {
    return [...el.querySelectorAll(sel)].filter((r) => r.getClientRects().length && !r.hasAttribute('disabled') && r.getAttribute('aria-disabled') !== 'true');
  }
  function arrowNav(el, sel, e) {
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp' && e.key !== 'Home' && e.key !== 'End' && e.key !== 'Enter' && e.key !== ' ') return false;
    const t = e.target;
    const typing = t && t.tagName === 'INPUT';
    const rows = rowsOf(el, sel);
    if (!rows.length) return false;
    if (e.key === 'Enter' || e.key === ' ') {
      if (typing || !rows.includes(t) || t.tagName === 'BUTTON') return false;
      e.preventDefault(); t.click(); return true;
    }
    if (typing && (e.key === 'Home' || e.key === 'End')) return false;
    const i = rows.indexOf(document.activeElement);
    let n;
    if (e.key === 'Home') n = 0;
    else if (e.key === 'End') n = rows.length - 1;
    else if (i < 0) n = e.key === 'ArrowDown' ? 0 : rows.length - 1;
    else n = Math.max(0, Math.min(rows.length - 1, i + (e.key === 'ArrowDown' ? 1 : -1)));
    e.preventDefault();
    const row = rows[n];
    if (!row.matches('button, a, input, [tabindex]')) row.tabIndex = -1;
    row.focus({ preventScroll: true });
    row.scrollIntoView({ block: 'nearest' });
    return true;
  }

  function close(el, done) {
    if (!el || el.classList.contains('is-closing')) return;
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    el.classList.add('is-closing');
    setTimeout(() => { el.classList.remove('is-closing'); if (done) done(); }, reduce ? 0 : (el.classList.contains('is-sheet') ? 220 : 120));
  }

  // ── adopt: someone else opens/closes it ─────────────────────────────────
  const adopted = new Map();
  function adopt(el, opts) {
    if (!el || adopted.has(el)) return;
    const o = Object.assign({ isOpen: (x) => x.classList.contains('open'), sheet: true, outside: true, width: 320 }, opts);
    const st = { open: false, scrim: null, side: null };
    adopted.set(el, st);
    el.classList.add('hx-pop', 'hx-pop-managed');
    const doClose = () => { if (o.close) o.close(); };
    const sync = () => {
      const isOpen = !!o.isOpen(el);
      if (isOpen === st.open) return;
      st.open = isOpen;
      if (isOpen) {
        st.side = null;
        if (el.parentElement !== document.body) document.body.appendChild(el);
        el.classList.remove('hx-pop-in');
        void el.offsetWidth;  // restart the open animation
        el.classList.add('hx-pop-in', 'is-open');
        layout();
        if (o.onOpen) o.onOpen(el);
        layout();
      } else {
        el.classList.remove('hx-pop-in', 'is-open', 'is-sheet');
        if (st.scrim) { const s = st.scrim; st.scrim = null; s.classList.remove('is-on'); setTimeout(() => s.remove(), 200); }
        if (o.onClose) o.onClose(el);
      }
    };
    function layout() {
      const sh = o.sheet && phone();
      sheet(el, sh);
      if (sh) {
        if (!st.scrim) st.scrim = makeScrim(doClose);
        return;
      }
      if (st.scrim) { st.scrim.remove(); st.scrim = null; }
      const a = o.anchor && o.anchor();
      if (!a || !a.getClientRects().length) return;
      const p = place(el, a, { width: o.width, minWidth: o.minWidth, maxWidth: o.maxWidth, maxHeight: o.maxHeight, side: st.side });
      st.side = p.side;
    }
    st.layout = layout;
    new MutationObserver(sync).observe(el, { attributes: true, attributeFilter: ['class', 'hidden'] });
    // phone sheet: drag down from its top edge to close (like the model picker)
    let y0 = null;
    el.addEventListener('touchstart', (e) => {
      if (!st.open || !el.classList.contains('is-sheet')) { y0 = null; return; }
      const top = el.getBoundingClientRect().top;
      y0 = e.touches[0].clientY - top < 64 && !(e.target.closest && e.target.closest('input, textarea')) ? e.touches[0].clientY : null;
    }, { passive: true });
    el.addEventListener('touchmove', (e) => { if (y0 != null) el.style.setProperty('--hxp-drag', Math.max(0, e.touches[0].clientY - y0) + 'px'); }, { passive: true });
    el.addEventListener('touchend', (e) => {
      if (y0 == null) return;
      const dy = e.changedTouches[0].clientY - y0; y0 = null;
      el.style.setProperty('--hxp-drag', '0px');
      if (dy > 90) doClose();
    });
    document.addEventListener('keydown', (e) => {
      if (!st.open) return;
      if (e.key === 'Escape') {
        e.preventDefault(); e.stopPropagation();
        doClose();
        const a = o.anchor && o.anchor();
        if (a && a.getClientRects().length) a.focus({ preventScroll: true });
        return;
      }
      if (o.rows && el.contains(e.target)) arrowNav(el, o.rows, e);
    }, true);
    if (o.outside) {
      document.addEventListener('pointerdown', (e) => {
        if (!st.open || el.contains(e.target)) return;
        const a = o.anchor && o.anchor();
        if (a && a.contains(e.target)) return;
        if (st.scrim && e.target === st.scrim) return;  // the scrim's own click closes it
        if (o.keep && o.keep(e.target)) return;
        doClose();
      }, true);
    }
    window.addEventListener('resize', () => { if (st.open) { st.side = null; layout(); } });
    sync();
  }

  // ── watch: someone else creates/removes it ──────────────────────────────
  const watchers = [];
  function watch(selector, opts) { watchers.push({ selector, opts: Object.assign({ sheet: true }, opts) }); scan(document.body); }
  const scrims = new WeakMap();
  function enhance(el, o) {
    if (el.dataset.hxPop) return;
    el.dataset.hxPop = '1';
    el.classList.add('hx-pop', 'hx-pop-managed', 'hx-pop-in');
    if (o.sheet && phone()) {
      el.classList.add('is-sheet');
      ['left', 'top', 'right', 'bottom', 'width'].forEach((k) => el.style.removeProperty(k));
      sheet(el, true);
      // a press on the scrim is a press outside the menu: the owner's outside handler closes it
      scrims.set(el, makeScrim(null));
    }
    if (o.rows) el.addEventListener('keydown', (e) => arrowNav(el, o.rows, e));
  }
  function scan(root) {
    if (!root || root.nodeType !== 1) return;
    for (const w of watchers) {
      if (root.matches(w.selector)) enhance(root, w.opts);
      root.querySelectorAll(w.selector).forEach((x) => enhance(x, w.opts));
    }
  }
  new MutationObserver((muts) => {
    if (!watchers.length) return;
    for (const m of muts) {
      m.addedNodes.forEach(scan);
      m.removedNodes.forEach((n) => {
        if (n.nodeType !== 1) return;
        const s = scrims.get(n);
        if (s) { scrims.delete(n); s.classList.remove('is-on'); setTimeout(() => s.remove(), 200); }
      });
    }
  }).observe(document.documentElement, { childList: true, subtree: true });

  window.hxPop = { safeTop, phone, place, adopt, watch, close, sheet, arrowNav, adopted: (el) => adopted.get(el) };

  // ── Tabbie-wide adoptions that live outside any one extension ──────────
  // Sidebar menus (sidebar.js: account menu, Recents ⋯): sidebar.js owns open/close/position.
  watch('.hx-more-menu', { rows: '.hx-more-item' });

  // Settings › Preferences › Hub model (upstream renderModelDropdown into #settingsModelDropdown).
  function settingsModel() {
    const dd = document.getElementById('settingsModelDropdown');
    if (!dd || adopted.has(dd)) return;
    adopt(dd, {
      anchor: () => document.getElementById('settingsModelChip'),
      close: () => { if (typeof window.closeSettingsModelDropdown === 'function') window.closeSettingsModelDropdown(); },
      width: 'anchor', minWidth: 340, maxWidth: 720, maxHeight: 620,
      rows: '.model-opt, .model-group.collapsible',
      // the + / clear buttons re-render the list; their presses stay inside
      keep: (t) => !!(t.closest && t.closest('#settingsModel')),
    });
  }
  settingsModel();
  setInterval(settingsModel, 1000);
})();
