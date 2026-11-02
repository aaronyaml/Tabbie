/* Hermes — Tabbie keyboard shortcuts and the "?" cheat sheet (plain JS, no build step).
 *   ⌥⌘M model picker · ⌥⌘A Agents sheet · ⌥⌘P Projects · ⌥⌘↓ jump to latest · ? this list
 * (Ctrl+Alt instead of ⌥⌘ off the Mac.) Chords match e.code, because ⌥ changes e.key on a Mac.
 * They fire from the page or the composer, never from another text field, and never while another
 * sheet or dialog is open. "?" fires only outside text fields. Upstream's own shortcuts (⌘K, ⌘/, ⌘,,
 * ⌘B, J/K) and the other extensions' (⌘↵ send now, ⌘\ sidebar) are listed, not rebound.
 */
(function () {
  'use strict';
  const MAC = /mac|iphone|ipad|ipod/i.test((navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || navigator.userAgent);
  const CMD = MAC ? '⌘' : 'Ctrl';
  const OPT = MAC ? '⌥' : 'Alt';
  const ARIA_MOD = MAC ? 'Alt+Meta+' : 'Control+Alt+';

  const icon = (n, s) => (typeof window.hxIcon === 'function' ? window.hxIcon(n, s) : '');
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  // ── actions ─────────────────────────────────────────────────────────────
  const visible = (el) => {
    if (!el || !el.isConnected || el.hidden) return false;
    if (typeof el.checkVisibility === 'function' && !el.checkVisibility({ opacityProperty: true, visibilityProperty: true })) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && r.right > 0 && r.bottom > 0 && r.left < innerWidth && r.top < innerHeight;
  };
  const projectsOpen = () => { try { return !!(window.hxProjects && window.hxProjects.isOpen()); } catch (_) { return false; } };
  const chip = () => document.getElementById('hxModelChip');
  const pill = () => document.querySelector('.hx-agents-pill');
  const jump = () => document.getElementById('hxJumpBottom');

  const ACTIONS = {
    KeyM() {
      const c = chip();
      if (!c || !visible(c) || projectsOpen()) return false;
      c.click();  // the chip toggles the picker and owns its focus handling
      return true;
    },
    KeyA() {
      const p = pill();
      if (!p || p.hidden || projectsOpen()) return false;
      p.click();
      return true;
    },
    KeyP() {
      const P = window.hxProjects;
      if (!P) return false;
      if (P.isOpen()) P.close(); else P.open();
      return true;
    },
    ArrowDown() {
      const j = jump();
      if (!j || projectsOpen()) return false;
      j.click();
      return true;
    },
  };

  // ── guards ──────────────────────────────────────────────────────────────
  function isText(t) {
    if (!t || t.nodeType !== 1) return false;
    if (t.isContentEditable) return true;
    if (t.tagName === 'TEXTAREA' || t.tagName === 'SELECT') return true;
    if (t.tagName === 'INPUT') return !/^(button|checkbox|radio|range|reset|submit|color|file|image)$/i.test(t.type || '');
    return !!(t.closest && t.closest('[contenteditable=""], [contenteditable="true"], [role="textbox"], .xterm'));
  }
  // Any other sheet or dialog on screen: model picker, Agents, project sheets, file viewer, upstream dialogs.
  function otherSheetOpen() {
    for (const el of document.querySelectorAll('[aria-modal="true"]')) {
      if (sheet && sheet.contains(el)) continue;
      if (visible(el)) return true;
    }
    return !!document.querySelector('.hx-as.is-open, .hx-mp-root.is-open');
  }

  // ── cheat sheet ─────────────────────────────────────────────────────────
  function sendNowKey() {
    return (window._sendKey || 'enter') === 'ctrl+enter' ? [CMD, '⇧', '↵'] : [CMD, '↵'];
  }
  function groups() {
    return [
      { title: 'Tabbie', rows: [
        [[OPT, CMD, 'M'], 'Choose the model'],
        [[OPT, CMD, 'A'], 'Agents in this chat'],
        [[OPT, CMD, 'P'], 'Projects'],
        [[OPT, CMD, '↓'], 'Jump to the latest reply'],
        [sendNowKey(), 'Send now, while a reply runs'],
        [[CMD, '\\'], 'Show or hide the sidebar'],
        [['?'], 'Show this list'],
      ] },
      { title: 'Chat', rows: [
        [[CMD, 'K'], 'New chat'],
        [[CMD, '/'], 'Go to the message box'],
        [[CMD, ','], 'Settings'],
        [[CMD, 'B'], 'Collapse the sidebar'],
        [['J'], 'Next chat'],
        [['K'], 'Previous chat'],
        [['Esc'], 'Close a panel or sheet'],
      ] },
    ];
  }
  let sheet = null, lastFocus = null;
  function build() {
    sheet = document.createElement('div');
    sheet.className = 'hx-sc';
    sheet.innerHTML = '<div class="hx-sc-scrim"></div>'
      + '<section class="hx-sc-panel" role="dialog" aria-modal="true" aria-labelledby="hxScTitle">'
      + '<div class="hx-sc-grab" aria-hidden="true"></div>'
      + '<header class="hx-sc-head"><h2 class="hx-sc-title" id="hxScTitle">Keyboard shortcuts</h2>'
      + `<button type="button" class="hx-sc-close" aria-label="Close">${icon('Cancel01Icon', 18)}</button></header>`
      + '<div class="hx-sc-body"></div></section>';
    sheet.querySelector('.hx-sc-scrim').addEventListener('click', () => toggle(false));
    sheet.querySelector('.hx-sc-close').addEventListener('click', () => toggle(false));
    sheet.addEventListener('transitionend', (e) => {
      if (e.target.classList.contains('hx-sc-panel') && !sheet.classList.contains('is-open')) sheet.classList.remove('is-mounted');
    });
    document.body.appendChild(sheet);
  }
  function paint() {
    const kbd = (keys) => keys.map((k) => `<kbd>${esc(k)}</kbd>`).join('');
    const label = (keys) => keys.join(MAC ? '' : '+');
    sheet.querySelector('.hx-sc-body').innerHTML = groups().map((g) => `<section class="hx-sc-group">
      <h3 class="hx-sc-gt">${esc(g.title)}</h3>
      <dl class="hx-sc-list">${g.rows.map(([keys, what]) => `<div class="hx-sc-row"><dt>${esc(what)}</dt>`
        + `<dd aria-label="${esc(label(keys))}">${kbd(keys)}</dd></div>`).join('')}</dl></section>`).join('');
  }
  const isOpen = () => !!(sheet && sheet.classList.contains('is-open'));
  function toggle(on) {
    if (on === isOpen()) return;
    if (!sheet) build();
    if (on) {
      lastFocus = document.activeElement;
      paint();
      sheet.classList.add('is-mounted');
      requestAnimationFrame(() => requestAnimationFrame(() => {
        sheet.classList.add('is-open');
        sheet.querySelector('.hx-sc-close').focus({ preventScroll: true });
      }));
    } else {
      sheet.classList.remove('is-open');
      if (matchMedia('(prefers-reduced-motion: reduce)').matches) sheet.classList.remove('is-mounted');
      if (lastFocus && lastFocus.isConnected && typeof lastFocus.focus === 'function') lastFocus.focus({ preventScroll: true });
      lastFocus = null;
    }
  }

  // ── keys ────────────────────────────────────────────────────────────────
  function onKey(e) {
    if (e.isComposing || e.keyCode === 229) return;
    if (isOpen()) {
      // the sheet is modal: Esc and ? close it, Tab stays on its one control, plain keys don't leak behind
      if (e.key === 'Escape' || (e.key === '?' && !e.metaKey && !e.ctrlKey && !e.altKey)) {
        e.preventDefault(); e.stopImmediatePropagation(); toggle(false); return;
      }
      if (e.key === 'Tab') { e.preventDefault(); sheet.querySelector('.hx-sc-close').focus(); return; }
      if (!e.metaKey && !e.ctrlKey) e.stopImmediatePropagation();
      return;
    }
    const t = e.target;
    const chord = MAC ? (e.metaKey && e.altKey && !e.ctrlKey) : (e.ctrlKey && e.altKey && !e.metaKey);
    if (chord && !e.shiftKey && ACTIONS[e.code]) {
      if (e.repeat) { e.preventDefault(); return; }
      if (isText(t) && !(t && t.id === 'msg')) return;
      // the same chord closes its own sheet; any other open sheet blocks the chord
      const own = (e.code === 'KeyM' && document.querySelector('.hx-mp-root.is-open'))
        || (e.code === 'KeyA' && document.querySelector('.hx-as.is-open .hx-as-scrim'));
      if (own) {
        e.preventDefault(); e.stopPropagation();
        if (e.code === 'KeyM') { const c = chip(); if (c) c.click(); } else own.click();
        return;
      }
      if (otherSheetOpen()) return;
      if (ACTIONS[e.code]()) { e.preventDefault(); e.stopPropagation(); }
      return;
    }
    if (e.key === '?' && !e.metaKey && !e.ctrlKey && !e.altKey && !e.repeat) {
      if (isText(t) || isText(document.activeElement) || otherSheetOpen()) return;
      e.preventDefault();
      toggle(true);
    }
  }

  // ── discoverability: aria-keyshortcuts on the targets (they are rebuilt now and then) ──
  function tag() {
    const set = (el, k) => { if (el && el.getAttribute('aria-keyshortcuts') !== k) el.setAttribute('aria-keyshortcuts', k); };
    set(chip(), ARIA_MOD + 'M');
    set(pill(), ARIA_MOD + 'A');
    document.querySelectorAll('#hxProjRail, #hxProjTab').forEach((el) => set(el, ARIA_MOD + 'P'));
    set(jump(), ARIA_MOD + 'ArrowDown');
  }

  function start() {
    window.addEventListener('keydown', onKey, true);
    tag();
    setInterval(tag, 2000);
    window.hxShortcuts = { open: () => toggle(true), close: () => toggle(false), isOpen };
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
