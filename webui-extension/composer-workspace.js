/* Hermes — composer workspace chip + dropdown, matched to the model picker (model-picker.*), using the
 * shared dropdown layer (dropdowns.js window.hxPop + dropdowns.css .hx-pop).
 * Upstream owns the data and every action (hermes-webui/static/panels.js: toggleComposerWsDropdown,
 * renderWorkspaceDropdownInto, switchToWorkspace, closeWsDropdown, syncWorkspaceDisplays); this file only
 * restyles and places it:
 *   - chip #composerWorkspaceChip: folder icon + label + chevron, same metrics as #hxModelChip; the
 *     "show workspace files" button beside it becomes a plain composer icon button with a files icon
 *   - dropdown #composerWsDropdown: adopted by hxPop (moved to <body>, above the chip or below it when the
 *     composer sits high, capped to the room; phone: bottom sheet + scrim; Escape / outside press close it;
 *     arrow keys move between rows), with a "Workspace" header; only the list scrolls
 *   - phone: opening the sheet hides the composer's config panel it came from; closing it closes that too
 *   - phone pill (#composerMobileConfigBtn, apple-skin.js label): same chevron as the model chip
 *   - "General (chat)" shows as "General" (chip, phone labels, list); the list shows names only
 */
(() => {
  'use strict';
  if (window.__hxComposerWs) return;
  window.__hxComposerWs = true;

  const icon = (n, s) => (typeof window.hxIcon === 'function' ? window.hxIcon(n, s) : '');
  // "General (chat)" reads as plain "General" everywhere this file draws (chip, phone pill, list)
  const CHAT = /\s*\(chat\)\s*$/i;
  const tidy = (el) => { if (el && CHAT.test(el.textContent)) el.textContent = el.textContent.replace(CHAT, ''); };

  // ── chip ────────────────────────────────────────────────────────────────
  // upstream rewrites these labels on every syncWorkspaceDisplays(); put the short name back each time
  const labels = new MutationObserver((muts) => muts.forEach((m) => tidy(m.target.nodeType === 1 ? m.target : m.target.parentElement)));
  const watched = new WeakSet();
  function watchLabel(id) {
    const el = document.getElementById(id);
    if (!el || watched.has(el)) return;
    watched.add(el);
    tidy(el);
    labels.observe(el, { childList: true, characterData: true, subtree: true });
  }

  function decorateChip() {
    watchLabel('composerWorkspaceLabel');
    watchLabel('composerMobileWorkspaceLabel');
    const chip = document.getElementById('composerWorkspaceChip');
    if (chip && !chip.querySelector('.hx-wsc-ic')) {
      const ic = document.createElement('span');
      ic.className = 'hx-wsc-ic';
      ic.setAttribute('aria-hidden', 'true');
      ic.innerHTML = icon('Folder01Icon', 15);
      chip.insertBefore(ic, chip.firstChild);
    }
    const chev = chip && chip.querySelector('.composer-workspace-chevron');
    if (chev && !chev.dataset.hxw) { chev.dataset.hxw = '1'; chev.innerHTML = icon('ArrowDown01Icon', 12); }
    const files = document.getElementById('btnWorkspacePanelToggle');
    const fi = files && files.querySelector('.composer-workspace-icon');
    if (fi && !fi.dataset.hxw) { fi.dataset.hxw = '1'; fi.innerHTML = icon('File01Icon', 16); }
    const pill = document.getElementById('composerMobileConfigBtn');
    if (pill && pill.querySelector('.hx-ws-label') && !pill.querySelector('.hx-wsc-chev')) {
      const c = document.createElement('span');
      c.className = 'hx-wsc-chev';
      c.setAttribute('aria-hidden', 'true');
      c.innerHTML = icon('ArrowDown01Icon', 12);
      pill.appendChild(c);
    }
  }

  // ── dropdown ────────────────────────────────────────────────────────────
  function decorate(dd) {
    if (!dd.querySelector(':scope > .hx-wsc-head')) {
      const head = document.createElement('div');
      head.className = 'hx-wsc-head';
      head.innerHTML = '<span class="hx-wsc-grab" aria-hidden="true"></span><span class="hx-wsc-title">Workspace</span>';
      dd.insertBefore(head, dd.firstChild);
    }
    const si = dd.querySelector('.ws-search-input');
    if (si && !si.dataset.hxw) { si.dataset.hxw = '1'; si.setAttribute('aria-label', 'Search workspaces'); }
    dd.querySelectorAll('.ws-opt-name').forEach(tidy);  // names only: paths are hidden in CSS (search still uses data-path)
  }

  let dd = null;
  const kids = new MutationObserver(() => { if (dd && dd.classList.contains('open')) decorate(dd); });
  function ensure() {
    decorateChip();
    const d = document.getElementById('composerWsDropdown');
    if (!d || d === dd || !window.hxPop) return;
    dd = d;
    dd.classList.add('hx-wsc');
    kids.observe(dd, { childList: true });
    window.hxPop.adopt(dd, {
      anchor: () => {
        const chip = document.getElementById('composerWorkspaceChip');
        return chip && chip.getClientRects().length ? chip : document.getElementById('composerWorkspaceGroup');
      },
      close: () => { if (typeof window.closeWsDropdown === 'function') window.closeWsDropdown(); },
      width: 320,
      rows: '.ws-opt:not([style*="none"])',
      keep: (t) => !!(t.closest && t.closest('#composerMobileWorkspaceAction, #composerMobileConfigPanel')),
      onOpen: (el) => {
        decorate(el);
        document.documentElement.classList.toggle('hx-wsc-sheet-open', el.classList.contains('is-sheet') || window.hxPop.phone());
        const cur = el.querySelector('.ws-opt.active');
        if (cur) cur.scrollIntoView({ block: 'nearest' });
      },
      onClose: () => {
        document.documentElement.classList.remove('hx-wsc-sheet-open');
        const panel = document.getElementById('composerMobileConfigPanel');
        if (window.hxPop.phone() && panel && panel.classList.contains('open') && typeof window.closeMobileComposerConfig === 'function') {
          window.closeMobileComposerConfig();
        }
      },
    });
  }

  ensure();
  setInterval(ensure, 700);
})();
