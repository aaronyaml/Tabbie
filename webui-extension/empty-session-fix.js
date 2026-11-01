/* Hermes — empty-session reload fix (plain JS, no build step).
 * Reloading /session/<id> for a session with no messages left "Loading conversation..." on screen
 * forever: upstream's renderMessages() keeps that placeholder while a load is in flight with zero
 * messages, and nothing re-renders once the load finishes. After each loadSession() settles (and on
 * any later DOM change), swap a stale placeholder for the normal empty state.
 * Also dresses the placeholder as a skeleton (a user bubble + a few reply lines with a soft shimmer)
 * instead of bare "Loading conversation..." text. The text stays for screen readers and for heal().
 */
(function () {
  'use strict';
  const PLACEHOLDER = 'Loading conversation...';

  function heal() {
    const inner = document.getElementById('msgInner');
    if (!inner || (inner.textContent || '').trim() !== PLACEHOLDER) return;
    let loading = null, msgs = [];
    try { loading = _loadingSessionId; } catch (_) {}
    try { msgs = S.messages || []; } catch (_) {}
    if (loading || msgs.length) return;
    inner.innerHTML = '';
    const empty = document.getElementById('emptyState');
    if (empty) empty.style.display = '';
  }

  const css = document.createElement('style');
  css.textContent = `
    .hx-skel { width: 100%; max-width: var(--msg-max, 780px); margin: 0 auto; padding: 28px 4px; display: flex;
      flex-direction: column; gap: 12px; animation: hx-skel-in .2s ease-out .15s both; }
    .hx-skel i { display: block; height: 12px; border-radius: 6px;
      background: linear-gradient(90deg, rgba(127,127,127,.10) 0%, rgba(127,127,127,.20) 50%, rgba(127,127,127,.10) 100%);
      background-size: 200% 100%; animation: hx-skel-sh 1.4s ease-in-out infinite; }
    .hx-skel .hx-skel-user { align-self: flex-end; width: 42%; height: 40px; border-radius: 18px; margin-bottom: 14px; }
    .hx-skel .hx-skel-name { width: 64px; height: 10px; margin-bottom: 4px; }
    .hx-skel-sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
    @keyframes hx-skel-sh { from { background-position: 100% 0; } to { background-position: -100% 0; } }
    @keyframes hx-skel-in { from { opacity: 0; } to { opacity: 1; } }
    @media (prefers-reduced-motion: reduce) { .hx-skel i { animation: none; } }`;
  document.head.appendChild(css);

  // Upstream injects a bare text div; swap its look for a skeleton (delay-faded so fast loads never flash it).
  function dress() {
    const inner = document.getElementById('msgInner');
    const ph = inner && inner.children.length === 1 && inner.firstElementChild;
    if (!ph || ph.classList.contains('hx-skel') || (ph.textContent || '').trim() !== PLACEHOLDER) return;
    const w = [92, 98, 76, 88, 54];
    ph.removeAttribute('style');
    ph.className = 'hx-skel';
    ph.setAttribute('aria-busy', 'true');
    ph.innerHTML = '<i class="hx-skel-user"></i><i class="hx-skel-name"></i>'
      + w.map((x) => `<i style="width:${x}%"></i>`).join('') + `<span class="hx-skel-sr">${PLACEHOLDER}</span>`;
  }

  function start() {
    if (typeof window.loadSession === 'function' && !window.loadSession.__hxEmptyFix) {
      const orig = window.loadSession;
      const wrapped = async function () {
        try { return await orig.apply(this, arguments); }
        finally { [0, 300, 1200].forEach((ms) => setTimeout(heal, ms)); }
      };
      wrapped.__hxEmptyFix = true;
      window.loadSession = wrapped;
    }
    const inner = document.getElementById('msgInner');
    if (inner) new MutationObserver(() => { dress(); setTimeout(heal, 400); }).observe(inner, { childList: true });
    dress();
    [500, 1500, 4000].forEach((ms) => setTimeout(heal, ms));
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
