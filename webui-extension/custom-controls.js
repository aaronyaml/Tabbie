/* Tabbie — custom dropdown list for every native <select> (plain JS, no build step).
 * Pairs with custom-controls.css, which also draws the checkboxes/radios (CSS only).
 *
 * How it stays compatible with upstream: the <select> itself is left exactly where it is, styled by
 * whatever CSS already targets it, and remains the source of truth. Only the OPEN state is replaced:
 * a press / tap / open-key on a select is intercepted (native picker suppressed) and our own list
 * is shown, built from the select's options. Choosing sets select.selectedIndex and dispatches
 * bubbling `input` + `change`, so inline onchange="…" handlers, addEventListener('change') and
 * code reading `.value` all keep working. Because the closed select still renders its own selected
 * text, the label can never go stale when code changes the value or swaps the options.
 * Listeners are delegated at window level, so selects added later are covered without scanning.
 * While open, the list follows option/value changes (MutationObserver + light poll) and closes if
 * the select goes away, hides or becomes disabled.
 * Skipped: select[multiple], select[size>1], and anything inside [data-hx-native] (opt-out).
 * Desktop: popover under the select (flips up near the bottom, clamped to the viewport).
 * Phone (<=640px): bottom sheet with a scrim.
 */
(() => {
  'use strict';
  if (window.__hxCustomControls) return;
  window.__hxCustomControls = true;

  const MARGIN = 8;          // keep this far from the viewport edges
  const GAP = 4;             // between select and list
  const MAX_H = 320;         // desktop list height cap
  const MIN_W = 160;
  const TYPE_MS = 700;       // type-ahead buffer window
  const TICK = '<svg viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2.8 7.3l2.7 2.7 5.7-6"/></svg>';

  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const phone = () => !!(window.matchMedia && window.matchMedia('(max-width: 640px)').matches);
  const selectOf = (t) => (t && t.nodeType === 1 && typeof t.closest === 'function' ? t.closest('select') : null);
  const eligible = (sel) => !!sel && sel.tagName === 'SELECT' && !sel.multiple && !(sel.size > 1) && !sel.closest('[data-hx-native]');

  let cur = null;            // { sel, pop, scrim, items, active, shown, sheet, mo, poll }
  let seq = 0;
  let typed = '';
  let typedAt = 0;
  let touch = null;          // { sel, x, y } for a touch that started on a select

  // ── model ───────────────────────────────────────────────────────────────
  // flat list of rows: { group } headers and { opt, index, label, disabled } options
  function readItems(sel) {
    const out = [];
    const add = (o, groupOff) => {
      if (o.hidden) return;
      out.push({ opt: o, index: o.index, label: o.label || o.textContent || '', disabled: !!(o.disabled || groupOff) });
    };
    for (const n of Array.from(sel.children)) {
      if (n.tagName === 'OPTGROUP') {
        out.push({ group: n.label || '' });
        for (const o of Array.from(n.children)) if (o.tagName === 'OPTION') add(o, n.disabled);
      } else if (n.tagName === 'OPTION') add(n, false);
    }
    return out;
  }

  // where the list goes: pure, so it can be tested without a layout engine
  function placement(rect, w, h, vw, vh) {
    const below = vh - rect.bottom - GAP - MARGIN;
    const above = rect.top - GAP - MARGIN;
    const want = Math.min(h, MAX_H);
    const up = below < want && above > below;
    const maxH = Math.max(Math.min(MAX_H, up ? above : below), Math.min(want, 96));
    const height = Math.min(h, maxH);
    const top = up ? Math.max(MARGIN, rect.top - GAP - height) : Math.min(rect.bottom + GAP, vh - MARGIN - height);
    const left = Math.max(MARGIN, Math.min(rect.left, vw - MARGIN - w));
    return { left, top, maxH, up };
  }

  const labelOf = (sel) => {
    const a = sel.getAttribute('aria-label');
    if (a) return a;
    const l = sel.labels && sel.labels[0];
    return (l && l.textContent.trim()) || sel.title || '';
  };
  const enabledAt = (i) => cur && cur.items[i] && cur.items[i].opt && !cur.items[i].disabled;

  // ── render ──────────────────────────────────────────────────────────────
  function render() {
    const { sel, pop } = cur;
    cur.items = readItems(sel);
    cur.shown = sel.selectedIndex;
    const title = cur.sheet && labelOf(sel) ? `<div class="hx-cc-title">${esc(labelOf(sel))}</div>` : '';
    const rows = cur.items.map((it, i) => (it.group != null
      ? `<div class="hx-cc-group" role="presentation">${esc(it.group)}</div>`
      : `<div class="hx-cc-opt" role="option" id="${pop.id}-${i}" data-i="${i}" aria-selected="${it.index === sel.selectedIndex}"${it.disabled ? ' aria-disabled="true"' : ''}>`
        + `${it.index === sel.selectedIndex ? TICK : ''}<span>${it.label.trim() ? esc(it.label) : '&nbsp;'}</span></div>`)).join('');
    pop.innerHTML = title + (rows || '<div class="hx-cc-empty">No options</div>');
    let a = cur.items.findIndex((it) => it.opt && it.index === sel.selectedIndex);
    if (cur.active != null && enabledAt(cur.active)) a = cur.active;
    if (a < 0 || !enabledAt(a)) a = step(-1, 1);
    setActive(a, true);
  }

  function place() {
    const { sel, pop } = cur;
    if (cur.sheet) return;
    const r = sel.getBoundingClientRect();
    const vw = document.documentElement.clientWidth || window.innerWidth;
    const vh = window.innerHeight || document.documentElement.clientHeight;
    pop.style.minWidth = Math.min(Math.max(r.width, MIN_W), vw - 2 * MARGIN) + 'px';
    pop.style.maxWidth = Math.max(vw - 2 * MARGIN, MIN_W) + 'px';
    pop.style.maxHeight = 'none';
    const p = placement(r, pop.offsetWidth, pop.scrollHeight, vw, vh);
    pop.style.maxHeight = p.maxH + 'px';
    pop.style.left = p.left + 'px';
    pop.style.top = p.top + 'px';
    pop.classList.toggle('is-up', p.up);
  }

  function setActive(i, scroll) {
    if (!cur) return;
    const prev = cur.pop.querySelector('.hx-cc-opt.is-active');
    if (prev) prev.classList.remove('is-active');
    cur.active = i;
    const el = i >= 0 ? cur.pop.querySelector(`[data-i="${i}"]`) : null;
    if (!el) { cur.sel.removeAttribute('aria-activedescendant'); return; }
    el.classList.add('is-active');
    cur.sel.setAttribute('aria-activedescendant', el.id);
    if (scroll) {  // manual scroll: scrollIntoView could also scroll the page behind
      const p = cur.pop;
      if (el.offsetTop < p.scrollTop) p.scrollTop = el.offsetTop - 4;
      else if (el.offsetTop + el.offsetHeight > p.scrollTop + p.clientHeight) p.scrollTop = el.offsetTop + el.offsetHeight - p.clientHeight + 4;
    }
  }

  // next enabled option from `from` in direction dir (±1), `count` steps; stays put at the ends
  function step(from, dir, count) {
    let i = from;
    let found = from;
    let n = count || 1;
    while (n > 0) {
      i += dir;
      if (i < 0 || i >= cur.items.length) break;
      if (enabledAt(i)) { found = i; n -= 1; }
    }
    return found;
  }

  // ── open / close / choose ───────────────────────────────────────────────
  function open(sel) {
    if (cur) close(false);
    if (!eligible(sel) || sel.disabled) return;
    const host = sel.closest('dialog[open]') || document.body;
    const sheet = phone();
    const pop = document.createElement('div');
    pop.className = 'hx-cc-pop hx-pop' + (sheet ? ' is-sheet' : '');  // .hx-pop: shared dropdown look (dropdowns.css)
    pop.id = 'hxCcPop' + (++seq);
    pop.setAttribute('role', 'listbox');
    pop.tabIndex = -1;
    const lbl = labelOf(sel);
    if (lbl) pop.setAttribute('aria-label', lbl);
    let scrim = null;
    if (sheet) {
      scrim = document.createElement('div');
      scrim.className = 'hx-cc-scrim';
      host.appendChild(scrim);
    }
    host.appendChild(pop);
    cur = { sel, pop, scrim, items: [], active: null, shown: -1, sheet, mo: null, poll: 0 };
    sel.setAttribute('aria-expanded', 'true');
    sel.setAttribute('aria-controls', pop.id);
    render();
    place();
    setActive(cur.active, true);
    cur.mo = new MutationObserver(() => { if (cur && cur.sel === sel) { render(); place(); } });
    cur.mo.observe(sel, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ['disabled', 'label', 'hidden', 'selected', 'value'] });
    cur.poll = setInterval(() => {
      if (!cur || cur.sel !== sel) return;
      if (!sel.isConnected || sel.disabled || !sel.getClientRects().length) { close(false); return; }
      if (sel.selectedIndex !== cur.shown) { cur.active = null; render(); }
    }, 250);
    const raf = window.requestAnimationFrame || ((f) => setTimeout(f, 16));
    raf(() => {
      if (!cur || cur.pop !== pop) return;
      pop.classList.add('is-open');
      if (scrim) scrim.classList.add('is-open');
    });
  }

  function close(refocus) {
    if (!cur) return;
    const { sel, pop, scrim, mo, poll, sheet } = cur;
    cur = null;
    if (mo) mo.disconnect();
    clearInterval(poll);
    pop.remove();
    if (scrim) scrim.remove();
    sel.setAttribute('aria-expanded', 'false');
    sel.removeAttribute('aria-controls');
    sel.removeAttribute('aria-activedescendant');
    if (refocus && !sheet && sel.isConnected) { try { sel.focus({ preventScroll: true }); } catch (_) { sel.focus(); } }
  }

  function choose(i) {
    if (!cur || !enabledAt(i)) return;
    const sel = cur.sel;
    const it = cur.items[i];
    const changed = sel.selectedIndex !== it.index;
    close(true);
    if (!changed) return;
    sel.selectedIndex = it.index;
    sel.dispatchEvent(new Event('input', { bubbles: true }));
    sel.dispatchEvent(new Event('change', { bubbles: true }));
  }

  function typeAhead(ch) {
    const now = Date.now();
    typed = now - typedAt < TYPE_MS ? typed + ch.toLowerCase() : ch.toLowerCase();
    typedAt = now;
    const same = typed.split('').every((c) => c === typed[0]);
    const q = same ? typed[0] : typed;
    const n = cur.items.length;
    const start = cur.active == null || cur.active < 0 ? 0 : cur.active + (same || typed.length === 1 ? 1 : 0);
    for (let k = 0; k < n; k++) {
      const i = (start + k) % n;
      if (enabledAt(i) && cur.items[i].label.trim().toLowerCase().startsWith(q)) { setActive(i, true); return; }
    }
  }

  // ── input ───────────────────────────────────────────────────────────────
  const OPEN_KEYS = new Set([' ', 'Enter', 'ArrowDown', 'ArrowUp', 'F4']);

  function onKey(e) {
    const t = e.target;
    if (cur && (t === cur.sel || cur.pop.contains(t))) {
      const k = e.key;
      let handled = true;
      if (k === 'ArrowDown') setActive(step(cur.active, 1), true);
      else if (k === 'ArrowUp') setActive(e.altKey ? cur.active : step(cur.active, -1), true);
      else if (k === 'Home') setActive(step(-1, 1), true);
      else if (k === 'End') setActive(step(cur.items.length, -1), true);
      else if (k === 'PageDown') setActive(step(cur.active, 1, 8), true);
      else if (k === 'PageUp') setActive(step(cur.active, -1, 8), true);
      else if (k === 'Enter') choose(cur.active);
      else if (k === ' ') { if (Date.now() - typedAt < TYPE_MS && typed) typeAhead(' '); else choose(cur.active); }
      else if (k === 'Escape') close(true);
      else if (k === 'Tab') { close(false); handled = false; }
      else if (k.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) typeAhead(k);
      else handled = false;
      if (handled) { e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation(); }
      return;
    }
    const sel = t && t.tagName === 'SELECT' ? t : null;
    if (!eligible(sel) || sel.disabled || e.ctrlKey || e.metaKey) return;
    if (!OPEN_KEYS.has(e.key) || (e.altKey && e.key !== 'ArrowDown' && e.key !== 'ArrowUp')) return;
    e.preventDefault();
    e.stopPropagation();
    open(sel);
  }

  function onMouseDown(e) {
    if (cur && cur.pop.contains(e.target)) { e.preventDefault(); return; }  // keep focus on the select
    const sel = selectOf(e.target);
    if (eligible(sel)) {
      if (e.button !== 0) return;
      e.preventDefault();                       // no native picker
      if (sel.disabled) return;
      if (cur && cur.sel === sel) { close(true); return; }
      try { sel.focus({ preventScroll: true }); } catch (_) { sel.focus(); }
      open(sel);
      return;
    }
    if (cur) close(false);
  }

  function onClick(e) {
    if (cur && cur.pop.contains(e.target)) {
      const row = e.target.closest('.hx-cc-opt');
      if (row) choose(Number(row.dataset.i));
      return;
    }
    if (cur && cur.scrim === e.target) { close(false); return; }
    if (eligible(selectOf(e.target))) e.preventDefault();
  }

  function onMove(e) {
    if (!cur || !cur.pop.contains(e.target)) return;
    const row = e.target.closest('.hx-cc-opt');
    if (row && Number(row.dataset.i) !== cur.active && enabledAt(Number(row.dataset.i))) setActive(Number(row.dataset.i), false);
  }

  // touch: a tap (not a scroll) on a select opens our list; cancelling touchend stops the
  // native picker plus the emulated mousedown/click/focus that would otherwise follow
  function onTouchStart(e) {
    const sel = selectOf(e.target);
    const p = e.touches && e.touches[0];
    touch = eligible(sel) && p ? { sel, x: p.clientX, y: p.clientY } : null;
  }
  function onTouchEnd(e) {
    const t = touch;
    touch = null;
    if (!t || selectOf(e.target) !== t.sel) return;
    const p = e.changedTouches && e.changedTouches[0];
    if (p && Math.hypot(p.clientX - t.x, p.clientY - t.y) > 10) return;   // was a scroll
    e.preventDefault();
    if (t.sel.disabled) return;
    if (cur && cur.sel === t.sel) close(false);
    else open(t.sel);
  }

  function onScroll(e) {
    if (!cur || cur.sheet) return;
    const t = e.target;
    // only a scroll that moves the select matters (a streaming chat scrolling elsewhere doesn't)
    if (t && t.nodeType === 1 && (cur.pop.contains(t) || !t.contains(cur.sel))) return;
    close(false);
  }

  window.addEventListener('keydown', onKey, true);
  window.addEventListener('mousedown', onMouseDown, true);
  window.addEventListener('click', onClick, true);
  window.addEventListener('mousemove', onMove, true);
  window.addEventListener('touchstart', onTouchStart, { capture: true, passive: true });
  window.addEventListener('touchend', onTouchEnd, { capture: true, passive: false });
  window.addEventListener('scroll', onScroll, true);
  window.addEventListener('resize', () => { if (cur && !cur.sheet) close(false); });
  window.addEventListener('blur', () => close(false));

  // small handle for debugging and tests
  window.hxCustomControls = { open, close, placement, readItems, isOpen: () => !!cur, current: () => (cur ? cur.sel : null) };
})();
