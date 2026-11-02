/* Tabbie — keep the previous reply when reopening a running chat (plain JS, no build step).
 * Opening a chat mid-run, upstream loadSession() drops "the current turn's" assistant rows before
 * rebuilding them from the live stream. It finds that turn by the last user row, but the new prompt is
 * still pending (not in the saved list yet), so the last user row is the PREVIOUS prompt and its
 * finished reply vanished until the run ended. Only drop assistant rows from the pending turn on.
 */
(function () {
  'use strict';
  const EPS = 1; // seconds of float / state.db drift

  function patch() {
    const orig = window._dropCurrentTurnAssistantMessages;
    if (typeof orig !== 'function' || orig.__hx) return !!orig;
    const fixed = function (messages) {
      let started = NaN;
      try { started = Number(S.session && S.session.pending_user_message && S.session.pending_started_at); } catch (_) {}
      if (!Array.isArray(messages) || !(started > 0)) return orig.apply(this, arguments);
      const out = orig.apply(this, arguments);
      // Put back assistant rows that finished before the pending prompt was sent.
      const kept = new Set(out);
      const ts = (m) => Number(m && (m.timestamp || m._ts)) || 0;
      return messages.filter((m) => kept.has(m) || (m && m.role === 'assistant' && !m._live && ts(m) > 0 && ts(m) < started - EPS));
    };
    fixed.__hx = true;
    window._dropCurrentTurnAssistantMessages = fixed;
    return true;
  }
  if (!patch()) {
    let n = 0;
    const t = setInterval(() => { if (patch() || ++n > 40) clearInterval(t); }, 250);
  }
})();
