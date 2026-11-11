/* Tabbie — a smaller frame around the chat (plain JS, no build step).
 *  1. Rail (desktop) and the drawer's icon row (phone): Chat, Motion, Projects, Settings only. Every
 *     other panel (Scheduled jobs, Kanban, Skills, Memory, Spaces, Profiles, Todos, Insights, Logs,
 *     Dashboard) moves into one "More" menu. Nothing is removed: each entry clicks the original
 *     button, so upstream and extension handlers run exactly as before. Panels upstream itself hides
 *     (Settings → tabs, hidden_tabs) stay hidden in More too.
 *  2. Composer: the profile chip and Saved prompts are hidden (CSS); profiles stay under More.
 *  3. Settings opens on Appearance, and "Conversation" (export/share/clear of the chat that was open
 *     before) becomes "This chat", last in the list, so it no longer looks like a global setting.
 *  4. Upstream's cancel copy ("Task cancelled: Task cancelled." + hint + details) becomes one line.
 */
(() => {
  'use strict';
  if (window.__hxDeclutter) return;
  window.__hxDeclutter = true;

  const icon = (n, s) => (typeof window.hxIcon === 'function' ? window.hxIcon(n, s) : '');
  const KEEP = new Set(['chat', 'settings']);
  const KEEP_IDS = new Set(['hxMotionRail', 'hxMotionTab', 'hxProjRail', 'hxProjTab', 'hxMoreRail', 'hxMoreTab']);

  // Buttons that belong in More: nav tabs that aren't kept, and that upstream hasn't hidden itself.
  function extras(bar) {
    return [...bar.querySelectorAll(':scope > .nav-tab')].filter((b) => {
      if (KEEP_IDS.has(b.id) || KEEP.has(b.dataset.panel)) return false;
      if (!b.dataset.panel && !b.hasAttribute('data-dashboard-link')) return false;
      return true;
    });
  }
  const label = (b) => b.getAttribute('aria-label') || b.dataset.tooltip || b.dataset.label || b.dataset.panel || '';
  const usable = (b) => !b.classList.contains('nav-tab-hidden') && b.style.display !== 'none';

  let menu = null, menuFor = null;
  function closeMenu() {
    if (!menu) return;
    menu.remove(); menu = null;
    if (menuFor) menuFor.setAttribute('aria-expanded', 'false');
    menuFor = null;
  }
  function openMenu(btn, bar) {
    if (menu && menuFor === btn) return closeMenu();
    closeMenu();
    const list = extras(bar).filter(usable);
    menu = document.createElement('div');
    menu.className = 'hx-more-menu';
    menu.setAttribute('role', 'menu');
    menu.setAttribute('aria-label', 'More');
    list.forEach((b) => {
      const it = document.createElement('button');
      it.type = 'button';
      it.className = 'hx-more-item' + (b.classList.contains('active') ? ' is-on' : '');
      it.setAttribute('role', 'menuitem');
      const svg = b.querySelector('svg');
      it.innerHTML = `<span class="hx-more-ic">${svg ? svg.outerHTML : ''}</span><span>${label(b).replace(/[<>&]/g, '')}</span>`;
      it.addEventListener('click', () => { closeMenu(); b.click(); });
      menu.appendChild(it);
    });
    if (!list.length) menu.innerHTML = '<div class="hx-more-empty">Nothing else here</div>';
    document.body.appendChild(menu);
    const r = btn.getBoundingClientRect();
    const phone = !bar.classList.contains('rail');
    if (phone) {
      menu.classList.add('is-sheet');
    } else {
      menu.style.left = `${Math.round(r.right + 8)}px`;
      const h = menu.offsetHeight;
      menu.style.top = `${Math.max(8, Math.min(Math.round(r.top), innerHeight - h - 8))}px`;
    }
    btn.setAttribute('aria-expanded', 'true');
    menuFor = btn;
    const first = menu.querySelector('.hx-more-item');
    if (first) first.focus({ preventScroll: true });
  }
  document.addEventListener('pointerdown', (e) => {
    if (menu && !menu.contains(e.target) && !(menuFor && menuFor.contains(e.target))) closeMenu();
  }, true);
  document.addEventListener('keydown', (e) => {
    if (!menu) return;
    if (e.key === 'Escape') { const f = menuFor; closeMenu(); if (f) f.focus(); return; }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      const items = [...menu.querySelectorAll('.hx-more-item')];
      const i = items.indexOf(document.activeElement);
      const n = items[(i + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length];
      if (n) { n.focus(); e.preventDefault(); }
    }
  });
  addEventListener('resize', closeMenu);

  function ensureMore(bar, id, rail) {
    let more = document.getElementById(id);
    const settings = bar.querySelector(':scope > [data-panel="settings"]');
    if (!more) {
      more = document.createElement('button');
      more.id = id;
      more.type = 'button';
      more.className = rail ? 'rail-btn nav-tab has-tooltip hx-more-btn' : 'nav-tab has-tooltip has-tooltip--bottom hx-more-btn';
      more.dataset.tooltip = 'More';
      if (!rail) more.dataset.label = 'More';
      more.setAttribute('aria-label', 'More');
      more.setAttribute('aria-haspopup', 'menu');
      more.setAttribute('aria-expanded', 'false');
      more.innerHTML = icon('DashboardSquare02Icon', rail ? 20 : 18);
      more.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); openMenu(more, bar); });
    }
    // Rail: just above the spacer that pushes Settings down. Drawer row: right before Settings.
    const anchor = rail ? (bar.querySelector(':scope > .rail-spacer') || settings) : settings;
    if (anchor && more.nextElementSibling !== anchor) anchor.before(more);
    else if (!anchor && more.parentNode !== bar) bar.appendChild(more);
    const ex = extras(bar);
    ex.forEach((b) => b.classList.add('hx-in-more'));
    more.hidden = !ex.some(usable);
    more.classList.toggle('active', ex.some((b) => b.classList.contains('active')));
  }

  function settingsOrder() {
    const item = document.querySelector('.side-menu-item[data-settings-section="conversation"]');
    if (!item || item.dataset.hxMoved) return;
    item.dataset.hxMoved = '1';
    const span = item.querySelector('span');
    if (span) { span.removeAttribute('data-i18n'); span.textContent = 'This chat'; }
    item.parentNode.appendChild(item);
    // Opening Settings lands on Appearance unless the reader picked a section this visit.
    try {
      if (typeof _currentSettingsSection !== 'undefined' && _currentSettingsSection === 'conversation') _currentSettingsSection = 'appearance';
      if (typeof _settingsSection !== 'undefined' && _settingsSection === 'conversation') _settingsSection = 'appearance';
    } catch (_) {}
  }

  // Upstream's cancel copy reads "Task cancelled: Task cancelled." + an italic hint + a details box
  // repeating it. One line instead: what happened and that nothing was thrown away.
  function cancelCopy() {
    document.querySelectorAll('#messages .msg-body > p:first-child > strong:first-child').forEach((st) => {
      if (st.textContent.trim() !== 'Task cancelled:') return;
      const p = st.parentElement, body = p.parentElement;
      const rest = p.textContent.replace('Task cancelled:', '').trim();
      p.textContent = /^task cancelled\.?$/i.test(rest) || !rest ? 'Stopped by you. Work done so far is kept.' : `Stopped: ${rest}`;
      p.classList.add('hx-stopped-line');
      const hint = p.nextElementSibling;
      if (hint && hint.tagName === 'P' && /cancelled by the user/i.test(hint.textContent)) hint.hidden = true;
      const det = body.querySelector(':scope > details.provider-error-details');
      if (det && /^\s*task cancelled\.?\s*$/i.test(det.querySelector('pre') ? det.querySelector('pre').textContent.replace('Copy', '') : '')) det.hidden = true;
    });
  }

  // Font size (Settings › Appearance): upstream only resizes chat text and a few lists, and Tabbie's own
  // sidebar, pages and composer are fixed px, so the choice barely showed. Tabbie scales the sidebar and the
  // page area as a whole instead (html[data-hx-scale] + declutter.css), and upstream's partial rules are
  // left out (its data-font-size attribute is never set) so text isn't scaled twice.
  function hookFontSize() {
    if (typeof window._applyFontSize !== 'function' || window._applyFontSize.__hx) return;
    const apply = (size) => {
      const de = document.documentElement;
      delete de.dataset.fontSize;
      if (size && size !== 'default') de.dataset.hxScale = size; else delete de.dataset.hxScale;
    };
    window._applyFontSize = apply;
    window._applyFontSize.__hx = true;
    let cur = null;
    try { cur = localStorage.getItem('hermes-font-size'); } catch (_) {}
    apply(document.documentElement.dataset.fontSize || cur || 'default');
  }
  hookFontSize();

  // Settings › Appearance "Skin": Tabbie has one look, so upstream's skin list comes up empty. Remove the
  // empty card; its hidden value stays in the page because upstream's save reads it.
  function dropSkin() {
    const grid = document.getElementById('skinPickerGrid');
    const field = grid && grid.closest('.settings-field');
    if (!field) return;
    const val = document.getElementById('settingsSkin');
    if (val) field.before(val);
    field.remove();
  }

  function sync() {
    hookFontSize();
    dropSkin();
    cancelCopy();
    const rail = document.querySelector('nav.rail');
    if (rail) ensureMore(rail, 'hxMoreRail', true);
    const side = document.querySelector('.sidebar-nav');
    if (side) ensureMore(side, 'hxMoreTab', false);
    settingsOrder();
  }
  // Panel switches only flip classes (not watched by hxDom): re-sync the More highlight after each.
  function hookSwitch() {
    if (typeof window.switchPanel !== 'function' || window.switchPanel.__hxMore) return !!(window.switchPanel && window.switchPanel.__hxMore);
    const orig = window.switchPanel;
    window.switchPanel = function () {
      document.documentElement.removeAttribute('data-hx-boot');  // the first-paint page (motion-boot.js) hands over
      const out = orig.apply(this, arguments);
      Promise.resolve(out).finally(() => requestAnimationFrame(sync));
      return out;
    };
    window.switchPanel.__hxMore = true;
    return true;
  }
  // upstream may already have opened the boot page itself: once <main> shows it, the boot marker is done
  (function bootDone() {
    const bp = document.documentElement.getAttribute('data-hx-boot');
    if (!bp) return;
    const m = document.querySelector('main.main');
    if (m && m.classList.contains('showing-' + bp)) document.documentElement.removeAttribute('data-hx-boot');
    else setTimeout(bootDone, 100);
  })();
  if (!hookSwitch()) { let n = 0; const t = setInterval(() => { if (hookSwitch() || ++n > 100) clearInterval(t); }, 100); }
  document.documentElement.classList.add('hx-declutter');
  sync();
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', sync);
  if (window.hxDom) window.hxDom.on(sync); else setInterval(sync, 1000);
})();
