/* Hermes — lazy new chat (plain JS, no build step).
 * "+" / ⌘K used to create a server session straight away and jump to /session/<id>, leaving empty
 * sessions behind. Now they just go to the home screen ("/"); upstream's send() already creates the
 * session when the first message goes out (it calls newSession() whenever S.session is null), so a
 * session only exists once you've actually said something. Project chips, onboarding and PWA
 * launches still call newSession() directly and are untouched.
 */
(function () {
  'use strict';
  function home(e) {
    if (typeof window.hxGoHome !== 'function') return; // instant-delete.js not loaded: keep upstream behaviour
    e.preventDefault();
    e.stopImmediatePropagation();
    window.hxGoHome();
    try { if (typeof closeMobileSidebar === 'function') closeMobileSidebar(); } catch (_) {}
    const box = document.getElementById('msg');
    if (box) box.focus();
  }
  // Capture phase: runs before upstream's onclick / keydown handlers.
  document.addEventListener('click', (e) => {
    const b = e.target.closest && e.target.closest('#btnNewChat, #btnTitlebarNewChat');
    if (b) home(e);
  }, true);
  document.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && !e.shiftKey && !e.altKey && (e.key === 'k' || e.key === 'K')) home(e);
  }, true);
})();
