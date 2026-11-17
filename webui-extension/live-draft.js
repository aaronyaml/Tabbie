/* Tabbie — the reply types itself out while the worker writes it (plain JS, no build step).
 * Claude Code runs with --include-partial-messages; the plugin keeps the text it is writing right now in
 * <request>/draft.json and the sidecar serves it at /draft?id=<session>. While a turn is live this polls it and
 * types the text into the chat, rendered as markdown, just above the live status line. When the finished reply
 * lands (the status line goes away) the draft is removed and the real message takes its place.
 * Text written before a tool call is narration: the draft clears and the step list picks it up.
 */
(() => {
  'use strict';
  if (window.__hxLiveDraft) return;
  window.__hxLiveDraft = true;

  const API = 'api/extensions/cc-agents/sidecar/';
  const POLL = 300;          // ms between draft fetches while a turn is live
  const CATCH_UP = 22;       // frames to type out whatever has arrived (about a third of a second)
  const sid = () => { try { return (typeof S !== 'undefined' && S && S.session && S.session.session_id) || ''; } catch (_) { return ''; } };

  let target = '';           // the full text known so far
  let shown = 0;             // characters typed out so far
  let el = null;
  let lastHtmlLen = -1;
  let raf = 0;
  let busy = false;
  let off = false;           // consent missing / sidecar down: stop asking until the next turn

  // what the reader should see while it's being written: no ATTACH lines, no follow-up or chart source
  function clean(t) {
    t = t.replace(/^[ \t]*ATTACH:[^\n]*\n?/gm, '');
    t = t.replace(/```tabbie-followups[\s\S]*?(```|$)/g, '');
    t = t.replace(/```chart[\s\S]*?(```|$)/g, '');
    return t.replace(/\n{3,}/g, '\n\n').replace(/\s+$/, '');
  }

  function md(t) {
    try { if (typeof window.renderMd === 'function') return window.renderMd(t); } catch (_) { /* fall through */ }
    const d = document.createElement('div');
    d.textContent = t;
    return '<p>' + d.innerHTML.replace(/\n\n+/g, '</p><p>').replace(/\n/g, '<br>') + '</p>';
  }

  function mount() {
    const status = document.getElementById('hxLiveStatus');
    if (!status) return null;
    if (!el || !el.isConnected) {
      el = document.createElement('div');
      el.className = 'hx-draft';
      el.setAttribute('aria-live', 'off');
      el.innerHTML = '<div class="msg-body hx-draft-body"></div>';
    }
    if (el.nextElementSibling !== status) status.parentNode.insertBefore(el, status);
    return el;
  }

  function unmount() {
    if (el) el.remove();
    el = null;
    target = '';
    shown = 0;
    lastHtmlLen = -1;
  }

  function paint() {
    raf = 0;
    if (!target) { unmount(); return; }
    const box = mount();
    if (!box) return;
    const rest = target.length - shown;
    if (rest > 0) shown += Math.max(2, Math.ceil(rest / CATCH_UP));
    if (shown > target.length) shown = target.length;
    // stop at a word edge so half-words don't flicker in
    let end = shown;
    if (end < target.length) { const sp = target.lastIndexOf(' ', end); if (sp > end - 12 && sp > 0) end = sp; }
    const text = clean(target.slice(0, end));
    if (text.length !== lastHtmlLen) {
      lastHtmlLen = text.length;
      const body = box.firstElementChild;
      body.innerHTML = md(text);
      let last = body.lastElementChild;
      while (last && /^(UL|OL|BLOCKQUOTE)$/.test(last.tagName) && last.lastElementChild) last = last.lastElementChild;
      const caret = document.createElement('span');
      caret.className = 'hx-draft-caret';
      caret.setAttribute('aria-hidden', 'true');
      (last && /^(P|LI|H[1-6])$/.test(last.tagName) ? last : body).appendChild(caret);
    }
    if (shown < target.length) raf = requestAnimationFrame(paint);
  }

  function take(text) {
    text = String(text || '');
    if (!text) { if (target) { target = ''; paint(); } return; }
    if (!text.startsWith(target.slice(0, Math.min(shown, target.length)))) { shown = 0; lastHtmlLen = -1; } // a new block began
    target = text;
    if (!raf) raf = requestAnimationFrame(paint);
  }

  async function poll() {
    const live = document.getElementById('hxLiveStatus');
    if (!live) { if (el || target) unmount(); off = false; return; }
    if (busy || off || document.hidden) return;
    const s = sid();
    if (!s) return;
    busy = true;
    try {
      const r = await fetch(API + 'draft?id=' + encodeURIComponent(s), { credentials: 'same-origin', cache: 'no-store' });
      if (r.status === 403 || r.status === 404) { off = true; return; }
      if (!r.ok) return;
      const j = await r.json();
      if (sid() === s && document.getElementById('hxLiveStatus')) take(j.text);
    } catch (_) { /* offline for a moment: the next poll tries again */ }
    finally { busy = false; }
  }

  setInterval(poll, POLL);
  window.hxLiveDraft = { take, unmount };
})();
