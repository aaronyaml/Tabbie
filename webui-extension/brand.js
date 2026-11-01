/* Tabbie — product name in the WebUI chrome (plain JS, no build step).
 * The assistant's name itself comes from the WebUI's own `bot_name` setting (Settings → Bot name =
 * "Tabbie"): message headers, "Message Tabbie…", the tab title and notifications all read it.
 * This file only covers what upstream hard-codes before/around that setting: the iOS home-screen
 * title meta, the boot-time titlebar/tab title, the "Latest … response" landmark and a few status
 * banners. It swaps text on those known nodes only; message bodies are never touched.
 */
(() => {
  'use strict';
  if (window.__hxBrand) return;
  window.__hxBrand = true;

  const NAME = 'Tabbie';
  const OLD = 'Hermes';

  // exact-text swaps on known upstream nodes: [selector, from, to]
  const TEXT = [
    ['#appTitlebarTitle', OLD, NAME],
    ['#agentHealthTitle', 'Hermes agent is not responding', `${NAME} isn't responding`],
    ['#offlineAutorefresh', 'I will refresh this page automatically when Hermes is reachable again.',
      `I'll refresh this page automatically when ${NAME} is reachable again.`],
  ];
  // attribute swaps: [selector, attribute]
  const ATTR = [
    ['meta[name="apple-mobile-web-app-title"]', 'content'],
    ['meta[name="application-name"]', 'content'],
    ['.assistant-turn[aria-label]', 'aria-label'],
    ['#emptyState .empty-logo svg[aria-label]', 'aria-label'],
    ['#settingsBotName', 'placeholder'],
    ['#agentHealthDismiss', 'aria-label'],
  ];
  const swap = (s) => s.replace(/\bHermes\b/g, NAME);

  function apply() {
    // upstream's fallback when settings haven't loaded (or bot_name is unset)
    if (!window._botName || window._botName === OLD) window._botName = NAME;
    if (document.title === OLD) document.title = NAME;
    else if (/ — Hermes$/.test(document.title)) document.title = document.title.replace(/ — Hermes$/, ` — ${NAME}`);
    const ta = document.getElementById('msg');
    if (ta && /^Message Hermes/.test(ta.placeholder || '')) ta.placeholder = ta.placeholder.replace(OLD, NAME);
    for (const [sel, from, to] of TEXT) {
      const el = document.querySelector(sel);
      if (el && el.textContent.trim() === from) el.textContent = to;
    }
    for (const [sel, attr] of ATTR) {
      document.querySelectorAll(sel).forEach((el) => {
        const v = el.getAttribute(attr);
        if (v && /\bHermes\b/.test(v)) el.setAttribute(attr, swap(v));
      });
    }
    document.querySelectorAll('.msg-role.assistant .msg-role-name').forEach((el) => {
      if (el.textContent === OLD) el.textContent = NAME;
    });
  }

  let queued = false;
  const later = () => { if (queued) return; queued = true; requestAnimationFrame(() => { queued = false; apply(); }); };
  function start() {
    apply();
    new MutationObserver(later).observe(document.documentElement, { childList: true, subtree: true, attributes: true,
      attributeFilter: ['aria-label', 'placeholder', 'content'] });
    new MutationObserver(later).observe(document.head, { childList: true, subtree: true, characterData: true });
  }
  apply();
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
