/* Hermes — keep the view on a streaming reply (plain JS, no build step).
 * Upstream has its own sticky-bottom follow, but on phones a tiny finger drag (8px) unpins it for
 * the rest of the reply. This is a simpler follower layered on top: while a turn is live and the
 * reader hasn't deliberately scrolled up, every growth of the transcript snaps to the bottom.
 * Scrolling up stops following; coming back to the bottom (or sending a message) resumes it.
 * Jump button: once the reader is well above the bottom, a round button floats centred just above
 * the message bar; tapping it glides to the bottom and resumes following. It replaces upstream's
 * corner arrow and the inline "↑ to response" buttons. If the transcript grows while a turn is live
 * and the reader is scrolled up, the button gains an accent dot and a "New" label until they return.
 */
(function () {
  'use strict';
  const LIVE = '[data-live-assistant="1"], [data-live-tool-call-group="1"], '
    + '[data-live-tool-worklog-group="1"], [data-tool-worklog-running="1"], #hxLiveStatus';
  let following = true, ours = 0, touchY = null, touching = false, lastTop = 0, lastSH = 0, stuckH = 0;
  // "New" dot: the transcript height when the reader stopped following, and the chat it belongs to.
  let baseH = 0, baseSid = null;

  const scroller = () => document.getElementById('messages');
  const gap = (el) => el.scrollHeight - el.scrollTop - el.clientHeight;
  function live() {
    let busy = false;
    try { busy = !!S.busy; } catch (_) {}
    return busy || !!document.querySelector(LIVE);
  }
  // Only follow real growth. Text that changes without growing (the status word's dots, timers)
  // must never move the view, or a reader starting to scroll up gets yanked back down.
  function stick(force) {
    const el = scroller();
    if (!el || !following || !live() || touching) return;
    if (!force && el.scrollHeight === stuckH) return;
    stuckH = el.scrollHeight;
    if (gap(el) < 2) return;
    ours = performance.now();
    el.scrollTop = el.scrollHeight;
  }

  function curSid() {
    try { return (S.session && S.session.session_id) || null; } catch (_) { return null; }
  }
  function unfollow() {
    following = false;
    const el = scroller();
    if (el && !baseH) { baseH = el.scrollHeight; baseSid = curSid(); }
  }

  let btn = null;
  function setNew(on) {
    if (!btn || btn.classList.contains('hx-jump-new') === on) return;
    btn.classList.toggle('hx-jump-new', on);
    const label = on ? 'New activity below, scroll to bottom' : 'Scroll to bottom';
    btn.setAttribute('aria-label', label);
    btn.title = label;
  }
  function clearNew() { baseH = 0; baseSid = null; setNew(false); }
  // Called on every transcript resize: flag growth that arrived while the reader was scrolled up.
  function checkNew() {
    const el = scroller();
    if (!btn || !el) return;
    if (baseH && baseSid !== curSid()) { clearNew(); return; }  // switched chats: the old height means nothing
    if (!following && !baseH && gap(el) > 200) { baseH = el.scrollHeight; baseSid = curSid(); }
    if (!following && baseH && live() && el.scrollHeight > baseH + 4) setNew(true);
  }
  function syncBtn() {
    const el = scroller();
    if (!btn || !el) return;
    const g = gap(el);
    const show = g > 200;
    btn.classList.toggle('hx-jump-show', show);
    btn.tabIndex = show ? 0 : -1;
    if (g < 60) clearNew();
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
    btn.innerHTML = (typeof window.hxIcon === 'function' ? window.hxIcon('ArrowDown01Icon', 20) : '')
      + '<span class="hx-jump-dot" aria-hidden="true"></span><span class="hx-jump-tx" aria-hidden="true">New</span>';
    btn.addEventListener('click', () => {
      const el = scroller();
      if (!el) return;
      following = true; stuckH = 0;
      clearNew();
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
    #hxJumpBottom svg { width: 20px; height: 20px; flex: none; }
    #hxJumpBottom .hx-jump-dot, #hxJumpBottom .hx-jump-tx { display: none; }
    #hxJumpBottom.hx-jump-new { width: auto; gap: 6px; padding: 0 14px 0 10px; }
    #hxJumpBottom.hx-jump-new .hx-jump-dot { display: block; flex: none; width: 7px; height: 7px; border-radius: 50%;
      background: var(--hx-accent, #3054FD); order: -1; animation: hxJumpDot .3s cubic-bezier(.34,1.56,.64,1); }
    #hxJumpBottom.hx-jump-new .hx-jump-tx { display: block; font: 600 13px/1 var(--font-ui, system-ui); letter-spacing: -.01em; }
    @keyframes hxJumpDot { from { transform: scale(0); } to { transform: none; } }
    @media (prefers-reduced-motion: reduce) {
      #hxJumpBottom { transition: opacity .15s linear; transform: translate(-50%, 0); }
      #hxJumpBottom.hx-jump-show, #hxJumpBottom:active { transform: translate(-50%, 0); }
      #hxJumpBottom.hx-jump-new .hx-jump-dot { animation: none; }
    }`;
  document.head.appendChild(css);

  function bind(el) {
    if (!el || el.dataset.hxFollow) return;
    el.dataset.hxFollow = '1';
    el.addEventListener('wheel', (e) => { if (e.deltaY < -2) unfollow(); }, { passive: true });
    el.addEventListener('touchstart', (e) => { touching = true; touchY = e.touches[0] ? e.touches[0].clientY : null; }, { passive: true });
    const lift = () => { touching = false; touchY = null; };
    el.addEventListener('touchend', lift, { passive: true });
    el.addEventListener('touchcancel', lift, { passive: true });
    el.addEventListener('touchmove', (e) => {
      // finger moving down = reading earlier content: let go at once
      if (touchY !== null && e.touches[0] && e.touches[0].clientY - touchY > 4) unfollow();
    }, { passive: true });
    el.addEventListener('scroll', () => {
      syncBtn();
      const top = el.scrollTop, sh = el.scrollHeight;
      if (performance.now() - ours >= 120) {
        // Moved up while the content stayed the same size: the reader did that (finger, momentum,
        // scrollbar, keys). A shrink also lowers scrollTop, but changes scrollHeight, so it's ignored.
        if (top < lastTop - 2 && sh === lastSH) unfollow();
        else if (gap(el) < 8 && !touching) { following = true; stuckH = 0; }
      }
      lastTop = top; lastSH = sh;
    }, { passive: true });
    const inner = document.getElementById('msgInner');
    if (inner && 'ResizeObserver' in window) new ResizeObserver(() => requestAnimationFrame(() => { stick(); checkNew(); syncBtn(); })).observe(inner);
    new MutationObserver(() => requestAnimationFrame(() => stick())).observe(el, { childList: true, subtree: true, characterData: true });
  }

  function start() {
    bind(scroller()); makeBtn();
    setInterval(() => { bind(scroller()); makeBtn(); syncBtn(); }, 2000);
    try {
      const ext = window.hermesExt && window.hermesExt.register('follow-scroll');
      if (ext && ext.events) ext.events.on('turn:start', () => { following = true; clearNew(); stick(true); });
    } catch (_) {}
    // Sending (Enter or the send button) always resumes following.
    addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey && e.target && e.target.id === 'msg') { following = true; stuckH = 0; } }, true);
    addEventListener('click', (e) => { if (e.target && e.target.closest && e.target.closest('#btnSend, .send-btn')) { following = true; stuckH = 0; } }, true);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
