/* Tabbie — share into Tabbie (plain JS, no build step). iOS can't make a web app a share target, so an
 * iOS Shortcut in the share sheet opens  <tabbie>/?share=<text or link>  (README: "Share to Tabbie").
 * This opens a new chat with that text in the composer, ready to add to and send. Nothing is sent by
 * itself, and the parameter is removed from the address bar right away.
 */
(() => {
  'use strict';
  const url = new URL(location.href);
  const text = (url.searchParams.get('share') || '').slice(0, 8000).trim();
  if (!text) return;
  url.searchParams.delete('share');
  history.replaceState(history.state, '', url.pathname + (url.search || '') + url.hash);

  let tries = 0;
  async function fill() {
    const box = document.getElementById('msg');
    if (!box || typeof newSession !== 'function') { if (++tries < 60) setTimeout(fill, 250); return; }
    try { await newSession(); } catch (_) {}
    setTimeout(() => {
      const m = document.getElementById('msg');
      if (!m) return;
      m.value = text + (m.value.trim() ? '\n\n' + m.value : '') + '\n\n';
      m.dispatchEvent(new Event('input', { bubbles: true }));
      m.focus();
      try { m.setSelectionRange(m.value.length, m.value.length); } catch (_) {}
    }, 300);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', fill, { once: true }); else fill();
})();
