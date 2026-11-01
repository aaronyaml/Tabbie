/* Hermes — sessions disappear from the sidebar the moment you delete them (plain JS, no build step).
 * Upstream's multi-select delete waits for every /api/session/delete to finish before re-rendering
 * the list. Wrap api(): as soon as a delete request starts, its row collapses away and stays hidden
 * through any re-render until the server answers. A refused delete is dropped from the pending set,
 * so upstream's own re-render after the failure brings that row back with its error toast.
 * Deleting also takes you straight to the new-chat home at "/" (instead of upstream opening the
 * next session): the open chat is cleared up front, which also skips upstream's "load the first
 * remaining session" step.
 */
(function () {
  'use strict';
  const pending = new Set();
  const css = document.createElement('style');
  css.textContent = `
    .session-item.hx-deleting { overflow: hidden; pointer-events: none;
      animation: hx-del .22s cubic-bezier(.32,.72,0,1) forwards; }
    .session-item.hx-gone { display: none !important; }
    @keyframes hx-del { to { opacity: 0; transform: translateX(-12px); max-height: 0; padding-top: 0; padding-bottom: 0; margin: 0; } }
    @media (prefers-reduced-motion: reduce) { .session-item.hx-deleting { animation: none; display: none; } }`;
  document.head.appendChild(css);

  function hide(root) {
    if (!pending.size) return;
    (root || document).querySelectorAll('.session-item').forEach((row) => {
      const idEl = row.dataset.sid ? row : row.querySelector('[data-sid]');
      if (!idEl || !pending.has(idEl.dataset.sid) || row.classList.contains('hx-deleting') || row.classList.contains('hx-gone')) return;
      // Rows already on screen animate out; rows re-rendered while the delete is in flight just stay gone.
      if (row.isConnected && row.getBoundingClientRect().height && !row.dataset.hxSeen) {
        row.dataset.hxSeen = '1';
        row.style.maxHeight = row.offsetHeight + 'px';
        row.classList.add('hx-deleting');
        row.addEventListener('animationend', () => row.classList.add('hx-gone'), { once: true });
      } else row.classList.add('hx-gone');
    });
  }

  let homeAt = 0;
  function goHome(force) {
    if (force !== true && Date.now() - homeAt < 1500) return; // one hop per batch
    homeAt = Date.now();
    try { if (typeof clearLiveToolCards === 'function') clearLiveToolCards(); } catch (_) {}
    try { if (typeof window._clearPendingSelections === 'function') window._clearPendingSelections(); } catch (_) {}
    try {
      S.session = null; S.messages = []; S.entries = []; S.toolCalls = []; S.activeStreamId = null; S.busy = false;
      localStorage.removeItem('hermes-webui-session');
    } catch (_) {}
    const base = new URL('./', document.baseURI || location.href).pathname;
    if (location.pathname !== base) history.pushState(null, '', base);
    const inner = document.getElementById('msgInner');
    if (inner) inner.innerHTML = '';
    const empty = document.getElementById('emptyState');
    if (empty) empty.style.display = '';
    try { if (typeof syncTopbar === 'function') syncTopbar(); } catch (_) {}
    try { if (typeof syncWorkspacePanelState === 'function') syncWorkspacePanelState(); } catch (_) {}
    try { if (typeof _hydrateTodosFromSession === 'function') _hydrateTodosFromSession(null); } catch (_) {}
    try { if (typeof updateQueueBadge === 'function') updateQueueBadge(); } catch (_) {}
    try { if (typeof renderSessionList === 'function') renderSessionList(); } catch (_) {}
  }
  window.hxGoHome = () => goHome(true);

  function wrap() {
    if (typeof window.api !== 'function' || window.api.__hxInstantDelete) return false;
    const orig = window.api;
    const wrapped = function (path, opts) {
      let sid = null;
      if (typeof path === 'string' && /\/api\/session\/delete$/.test(path) && opts && opts.body) {
        try { sid = JSON.parse(opts.body).session_id || null; } catch (_) {}
      }
      if (sid) { pending.add(String(sid)); hide(); goHome(); }
      const p = orig.apply(this, arguments);
      if (sid && p && typeof p.then === 'function') {
        // Success: the server no longer lists it, so the next render won't draw it. Failure: show it again.
        p.then(() => setTimeout(() => pending.delete(String(sid)), 4000), () => pending.delete(String(sid)));
      }
      return p;
    };
    wrapped.__hxInstantDelete = true;
    window.api = wrapped;
    return true;
  }

  function start() {
    if (!wrap()) { const t = setInterval(() => { if (wrap()) clearInterval(t); }, 300); }
    new MutationObserver(() => hide()).observe(document.body, { childList: true, subtree: true });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
