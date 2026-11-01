/* Hermes — sidebar collapse/expand button, top left (plain JS, no build step).
 * Desktop/tablet only (>640px): phones already have the hamburger drawer. Drives upstream's own
 * toggleSidebar(), so the state persists the same way as its other collapse paths.
 * Shortcut: Cmd/Ctrl + \ (same as many editors).
 * Also hides upstream's top title bar ("Untitled 0" + avatar) at these widths; the button floats
 * at the top-left of the chat area, just right of the sidebar, like ChatGPT's.
 */
(function () {
  'use strict';
  const icon = (name) => (typeof window.hxIcon === 'function' ? window.hxIcon(name, 20) : '');
  const collapsed = () => {
    try { if (typeof _isSidebarCollapsed === 'function') return _isSidebarCollapsed(); } catch (_) {}
    const l = document.querySelector('.layout');
    return !!(l && l.classList.contains('sidebar-collapsed'));
  };

  function sync(btn) {
    const c = collapsed();
    const want = c ? 'PanelLeftIcon' : 'PanelLeftCloseIcon';
    if (btn.dataset.icon !== want) { btn.innerHTML = icon(want); btn.dataset.icon = want; }
    btn.setAttribute('aria-label', c ? 'Show sidebar' : 'Hide sidebar');
    btn.title = c ? 'Show sidebar (⌘\\)' : 'Hide sidebar (⌘\\)';
    btn.setAttribute('aria-expanded', String(!c));
    btn.style.display = innerWidth > 640 ? 'inline-flex' : 'none';
    place(btn);
  }

  // Float just right of whatever is showing on the left (rail, or rail + sidebar panel).
  function place(btn) {
    const sb = document.querySelector('.sidebar'), rail = document.querySelector('.rail');
    let x = 0;
    [rail, sb].forEach((el) => {
      const r = el && el.getBoundingClientRect();
      if (r && r.width > 4 && r.right > x) x = r.right;
    });
    btn.style.left = `${Math.round(x) + 10}px`;
  }

  const css = document.createElement('style');
  css.textContent = '@media (min-width: 641px) { .app-titlebar { display: none !important; } }';
  document.head.appendChild(css);

  function toggle() {
    if (typeof toggleSidebar === 'function') toggleSidebar();
  }

  function start() {
    if (document.getElementById('hxSidebarToggle')) return;
    const btn = document.createElement('button');
    btn.id = 'hxSidebarToggle';
    btn.type = 'button';
    btn.style.cssText = 'position:fixed;top:10px;left:58px;z-index:60;'
      + 'align-items:center;justify-content:center;width:32px;height:32px;'
      + 'border:0;border-radius:8px;background:transparent;color:inherit;opacity:.72;cursor:pointer;'
      + '-webkit-app-region:no-drag;transition:background .15s,opacity .15s';
    btn.addEventListener('mouseenter', () => { btn.style.opacity = '1'; btn.style.background = 'var(--hx-fill, rgba(127,127,127,.12))'; });
    btn.addEventListener('mouseleave', () => { btn.style.opacity = '.72'; btn.style.background = 'transparent'; });
    btn.addEventListener('click', () => { toggle(); sync(btn); setTimeout(() => place(btn), 300); });
    document.body.appendChild(btn);
    sync(btn);
    // The sidebar slides; re-place during and after its transition.
    const sbEl = document.querySelector('.sidebar');
    if (sbEl) sbEl.addEventListener('transitionend', () => place(btn));
    [60, 160, 320].forEach((ms) => setTimeout(() => place(btn), ms));
    const layout = document.querySelector('.layout');
    if (layout) new MutationObserver(() => sync(btn)).observe(layout, { attributes: true, attributeFilter: ['class'] });
    addEventListener('resize', () => sync(btn));
    addEventListener('keydown', (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === '\\' && innerWidth > 640) { e.preventDefault(); toggle(); sync(btn); setTimeout(() => place(btn), 300); }
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
