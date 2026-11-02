/* Tabbie — readable session lists (plain JS, no build step).
 * Upstream titles a Claude Code session by its first user message (whitespace collapsed, 80 chars).
 *  - Hub worker sessions (`claude -p` started by the command centre) start with the hub's brief,
 *    "COMMAND CENTRE REQUEST <id> Route: …". They are plumbing: the parent chat already shows the route
 *    line and the relayed result, and there is no link to them anywhere in the UI. They keep session
 *    persistence (they get resumed), so they are hidden here instead: in the sidebar (all / CLI
 *    sessions, search) and in Home's "Jump back in". The session you are viewing is never hidden; it
 *    is shown as "Worker: <route reason>".
 *  - Interactive sessions whose first message was a paste start with `<pasted_content id="…">`: the
 *    envelope is stripped and the pasted text is the title.
 * window.hxSessionTitles = { isWorker(s|title), clean(title), title(s) } for other extension files.
 */
(() => {
  'use strict';
  if (window.__hxSessionTitles) return;
  window.__hxSessionTitles = true;

  // Openings of every prompt the command centre hands a worker (plugin/command-centre/__init__.py
  // _claude_prompt / _takeover_prompt / _codex_worker_prompt, providers.py PLANNER_ROLE / WORKER_RULES).
  const WORKER = [
    /^COMMAND CENTRE REQUEST\b/,
    /^You are the execution worker of the user's command centre\b/,
    /^You are taking over from Claude Code, which stopped\b/,
    /^You are the PLANNER in a personal AI command centre\b/,
    /^<message>/,  // the sidecar's own title calls (sidecar.py make_title) saved before --no-session-persistence
  ];
  const PASTE_OPEN = /<pasted_content\b[^>]*>?/gi;   // the 80-char cut can land inside the tag
  const PASTE_CLOSE = /<\/pasted_content\s*>?/gi;

  const raw = (s) => String((s && typeof s === 'object' ? (s.display_title || s._state_db_title || s.title) : s) || '').trim();
  const isWorker = (s) => { const t = raw(s); return WORKER.some((re) => re.test(t)); };

  function workerTitle(t) {
    const m = /Route:\s*(?:Codex\s*→\s*approval\s*→\s*)?[^—]+?—\s*(.+)$/.exec(t);
    if (m && m[1].trim()) return `Worker: ${m[1].trim()}`;
    const id = /^COMMAND CENTRE REQUEST\s+(\S+)/.exec(t);
    return id ? `Worker: request ${id[1]}` : 'Worker session';
  }

  // Readable title, or the input unchanged when there's nothing to fix.
  function clean(t) {
    t = String(t || '');
    if (WORKER.some((re) => re.test(t.trim()))) return workerTitle(t.trim());
    if (/^\s*<pasted_content\b/i.test(t)) {
      const inner = t.replace(PASTE_OPEN, ' ').replace(PASTE_CLOSE, ' ').replace(/^[\s#>*_-]+/, '').replace(/\s+/g, ' ').trim();
      return inner || 'Pasted text';
    }
    return t;
  }
  const title = (s) => clean(raw(s));
  window.hxSessionTitles = { isWorker, clean, title };

  const activeSid = () => { try { return (S.session && S.session.session_id) || ''; } catch (_) { return ''; } };

  // ── data level: before upstream paints the sidebar ──
  function fixRows(rows) {
    if (!Array.isArray(rows)) return rows;
    const active = activeSid();
    const out = rows.filter((s) => !(s && isWorker(s) && s.session_id !== active));
    out.forEach((s) => {
      if (!s) return;
      const r = raw(s), c = clean(r);
      if (c !== r) s.display_title = c; // display only: these rows are read-only CLI imports
    });
    return out;
  }
  function wrapRows() {
    const orig = window._sessionRowsWithActiveEphemeralSession;
    if (typeof orig !== 'function') return false;
    if (orig.__hxTitles) return true;
    const w = function () { return fixRows(orig.apply(this, arguments)); };
    w.__hxTitles = true;
    Object.keys(orig).forEach((k) => { w[k] = orig[k]; }); // keep other wrappers' markers (motion.js)
    window._sessionRowsWithActiveEphemeralSession = w;
    try { if (typeof renderSessionListFromCache === 'function') renderSessionListFromCache(); } catch (_) {}
    return true;
  }

  // ── DOM level: anything that slips through (content-search hits, other render paths) ──
  function fixDom() {
    const active = activeSid();
    document.querySelectorAll('.session-item, .session-child-session').forEach((el) => {
      const tt = el.querySelector('.session-title');
      if (!tt) return;
      const sid = (tt.dataset && tt.dataset.sid) || el.dataset.sid || '';
      const text = tt.textContent || '';
      const hide = isWorker(text) && sid !== active;
      if (hide !== (el.dataset.hxWorker === '1')) {
        if (hide) { el.dataset.hxWorker = '1'; el.style.setProperty('display', 'none', 'important'); }
        else { delete el.dataset.hxWorker; el.style.removeProperty('display'); }
      }
      if (hide) return;
      const c = clean(text);
      if (c !== text) { tt.textContent = c; }
    });
  }

  let queued = false;
  function schedule() {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => { queued = false; fixDom(); });
  }
  function start() {
    if (!wrapRows()) { const iv = setInterval(() => { if (wrapRows()) clearInterval(iv); }, 300); setTimeout(() => clearInterval(iv), 30000); }
    new MutationObserver(schedule).observe(document.body, { childList: true, subtree: true, characterData: true });
    fixDom();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
