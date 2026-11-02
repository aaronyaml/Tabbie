/* Tabbie — reply model label. Upstream labels every reply with the hub model (the one that routes the
 * chat and relays the result, e.g. "GPT-6.1 Sol"), even when Claude Code or Codex did the work with the
 * model picked in the composer. The route line at the top of each reply names the worker
 * ("Route: Claude Opus 5.5 (low) — …"), so the label shows that instead; hover for the hub model.
 * "Route: Tabbie — …" replies were answered by the hub itself and keep its label.
 */
(() => {
  'use strict';
  if (window.__hxWorkerLabel) return;
  window.__hxWorkerLabel = true;

  const CHIPS = '.lf-model, .msg-gateway-inline, .msg-used-model-inline';
  // "Route: Claude Opus 5.5 (low) — x", "Route: Astra → approval → Codex GPT-6.1 Sol — x", "Route: Astra (takeover) — x"
  const ROUTE = /Route:\s*(?:Astra\s*→\s*approval\s*→\s*)?([^—\n]+?)\s+—/;

  function worker(turn) {
    const m = ROUTE.exec((turn.textContent || '').slice(0, 400));
    if (!m) return null;
    const who = m[1].trim();
    if (/^Tabbie\b/.test(who)) return null;
    if (/^Astra \(takeover\)$/.test(who)) return 'Codex';
    return who.replace(/^Claude\s+(?=\S)/, '').replace(/^Codex\s+(?=\S)/, '');
  }

  function fix() {
    document.querySelectorAll('.assistant-turn').forEach((turn) => {
      const chips = turn.querySelectorAll(CHIPS);
      if (!chips.length) return;
      const w = worker(turn);
      if (!w) return;
      chips.forEach((c) => {
        if (c.dataset.hxWorker === w && c.textContent === w) return;
        const hub = c.dataset.hxHub || c.textContent;
        c.dataset.hxHub = hub;
        c.dataset.hxWorker = w;
        c.textContent = w;
        c.title = `Work done by ${w}. Routed and relayed by the hub (${hub}).`;
      });
    });
  }

  if (window.hxDom) window.hxDom.on(fix);  // shared, throttled page watcher (hx-dom.js)
  else {
    let queued = false;
    new MutationObserver(() => {
      if (queued) return;
      queued = true;
      requestAnimationFrame(() => { queued = false; fix(); });
    }).observe(document.body, { childList: true, subtree: true, characterData: true });
  }
  fix();
})();
