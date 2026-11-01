/* Hermes — keep the view on a streaming reply (plain JS, no build step).
 * Upstream has its own sticky-bottom follow, but on phones a tiny finger drag (8px) unpins it for
 * the rest of the reply. This is a simpler follower layered on top: while a turn is live and the
 * reader hasn't deliberately scrolled up, every growth of the transcript snaps to the bottom.
 * Scrolling up stops following; coming back to the bottom (or sending a message) resumes it.
 * Jump button: once the reader is well above the bottom, a round button floats centred just above
 * the message bar; tapping it glides to the bottom and resumes following. It replaces upstream's
 * corner arrow and the inline "↑ to response" buttons.
 */
(function () {
  'use strict';
  const LIVE = '[data-live-assistant="1"], [data-live-tool-call-group="1"], '
    + '[data-live-tool-worklog-group="1"], [data-tool-worklog-running="1"], #hxLiveStatus';
  let following = true, ours = 0, touchY = null;

  const scroller = () => document.getElementById('messages');
  const gap = (el) => el.scrollHeight - el.scrollTop - el.clientHeight;
  function live() {
    let busy = false;
    try { busy = !!S.busy; } catch (_) {}
    return busy || !!document.querySelector(LIVE);
  }
  function stick() {
    const el = scroller();
    if (!el || !following || !live() || gap(el) < 2) return;
    ours = performance.now();
    el.scrollTop = el.scrollHeight;
  }

  let btn = null;
  function syncBtn() {
    const el = scroller();
    if (!btn || !el) return;
    const show = gap(el) > 200;
    btn.classList.toggle('hx-jump-show', show);
    btn.tabIndex = show ? 0 : -1;
  }
  function makeBtn() {
    const shell = document.querySelector('.messages-shell');
    if (!shell || document.getElementById('hxJumpBottom')) return;
    btn = document.createElement('button');
    btn.id = 'hxJumpBottom';
    btn.type = 'button';
    btn.setAttribute('aria-label', 'Scroll to bottom');
    btn.title = 'Scroll to bottom';
    btn.tabIndex = -1;
    btn.innerHTML = typeof window.hxIcon === 'function' ? window.hxIcon('ArrowDown01Icon', 20) : '';
    btn.addEventListener('click', () => {
      const el = scroller();
      if (!el) return;
      following = true;
      el.scrollTo({ top: el.scrollHeight, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
    });
    shell.appendChild(btn);
    syncBtn();
  }
  const css = document.createElement('style');
  css.textContent = `
    #scrollToBottomBtn, .msg-question-jump-btn { display: none !important; }
    .messages-shell { position: relative; }
    #hxJumpBottom { position: absolute; left: 50%; bottom: 12px; z-index: 30; width: 38px; height: 38px;
      display: inline-flex; align-items: center; justify-content: center; padding: 0; border-radius: 999px;
      border: 0.5px solid var(--hx-hair, rgba(127,127,127,.25)); color: var(--hx-text, inherit);
      background: var(--hx-glass, rgba(30,30,34,.8)); -webkit-backdrop-filter: saturate(180%) blur(20px); backdrop-filter: saturate(180%) blur(20px);
      box-shadow: 0 6px 20px -6px rgba(0,0,0,.35); cursor: pointer;
      opacity: 0; pointer-events: none; transform: translate(-50%, 8px) scale(.92);
      transition: opacity .2s ease, transform .25s cubic-bezier(.34,1.56,.64,1); }
    #hxJumpBottom.hx-jump-show { opacity: 1; pointer-events: auto; transform: translate(-50%, 0) scale(1); }
    #hxJumpBottom:active { transform: translate(-50%, 0) scale(.94); }
    #hxJumpBottom svg { width: 20px; height: 20px; }`;
  document.head.appendChild(css);

  function bind(el) {
    if (!el || el.dataset.hxFollow) return;
    el.dataset.hxFollow = '1';
    el.addEventListener('wheel', (e) => { if (e.deltaY < -2) following = false; }, { passive: true });
    el.addEventListener('touchstart', (e) => { touchY = e.touches[0] ? e.touches[0].clientY : null; }, { passive: true });
    el.addEventListener('touchmove', (e) => {
      // finger moving down = reading earlier content
      if (touchY !== null && e.touches[0] && e.touches[0].clientY - touchY > 24) following = false;
    }, { passive: true });
    el.addEventListener('scroll', () => {
      syncBtn();
      if (performance.now() - ours < 120) return;
      if (gap(el) < 60) following = true;
    }, { passive: true });
    const inner = document.getElementById('msgInner');
    if (inner && 'ResizeObserver' in window) new ResizeObserver(() => requestAnimationFrame(() => { stick(); syncBtn(); })).observe(inner);
    new MutationObserver(() => requestAnimationFrame(stick)).observe(el, { childList: true, subtree: true, characterData: true });
  }

  function start() {
    bind(scroller()); makeBtn();
    setInterval(() => { bind(scroller()); makeBtn(); syncBtn(); }, 2000);
    try {
      const ext = window.hermesExt && window.hermesExt.register('follow-scroll');
      if (ext && ext.events) ext.events.on('turn:start', () => { following = true; stick(); });
    } catch (_) {}
    // Sending (Enter or the send button) always resumes following.
    addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey && e.target && e.target.id === 'msg') following = true; }, true);
    addEventListener('click', (e) => { if (e.target && e.target.closest && e.target.closest('#btnSend, .send-btn')) following = true; }, true);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
