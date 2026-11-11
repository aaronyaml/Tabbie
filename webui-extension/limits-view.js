/* Tabbie — /limit as native cards (plain JS, no build step). Pairs with limits-view.css.
 * The limits plugin (~/hermes/plugin/limits) answers /limit in the WebUI with a ```tabbie-limits
 * JSON block followed by a readable markdown fallback. This turns that reply into one card per
 * service: plain-language rows, slim meters that warm up near the limit, the closest-to-limit window marked,
 * credits as a number, and not-connected services as a quiet line with the fix. The raw block and
 * fallback stay in the message (copy still gets them) but are hidden.
 * Also understands the older table-only reply ("| Service | Window | Used | Resets in |"), so it
 * works before the WebUI process reloads the plugin.
 */
(() => {
  'use strict';
  if (window.__hxLimits) return;
  window.__hxLimits = true;

  const FENCE = 'tabbie-limits';
  const icon = (n, s) => (typeof window.hxIcon === 'function' ? window.hxIcon(n, s) : '');
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const num = (n) => Number(n).toLocaleString('en-GB');
  const pctTxt = (p) => `${Math.round(Number(p) * 10) / 10}%`;

  // ── parsing ─────────────────────────────────────────────────────────────
  function fromJson(raw) {
    const m = raw.match(new RegExp('```' + FENCE + '\\s*\\n([\\s\\S]*?)\\n```'));
    if (!m) return null;
    let d;
    try { d = JSON.parse(m[1]); } catch (_) { return null; }
    if (!d || !Array.isArray(d.rows)) return null;
    return {
      asOf: (Number(d.as_of) || Date.now() / 1000) * 1000,
      rows: d.rows.map((r) => ({
        service: r.service || '', window: r.window || '—', pct: r.pct, used: r.used, limit: r.limit,
        resetsAt: r.resets_at ? Date.parse(r.resets_at) : null, note: r.note || '', stale: r.stale_seconds,
      })),
    };
  }

  const durMs = (t) => {
    let ms = 0;
    String(t).replace(/(\d+)\s*([dhm])/g, (_, n, u) => { ms += n * { d: 864e5, h: 36e5, m: 6e4 }[u]; return ''; });
    return ms;
  };

  function fromTable(raw) {
    if (!/\|\s*Service\s*\|\s*Window\s*\|\s*Used\s*\|\s*Resets in\s*\|/.test(raw)) return null;
    let asOf = Date.now();
    const at = raw.match(/As of (\d{1,2}):(\d{2})/);
    if (at) { const d = new Date(); d.setHours(+at[1], +at[2], 0, 0); if (d - Date.now() > 6e4) d.setDate(d.getDate() - 1); asOf = +d; }
    const rows = [];
    raw.split('\n').filter((l) => /^\|/.test(l.trim())).slice(2).forEach((l) => {
      const c = l.trim().replace(/^\||\|$/g, '').split('|').map((x) => x.trim());
      if (c.length < 4) return;
      const [service, window, usedCell, resets] = c;
      const r = { service, window, pct: null, used: null, limit: null, resetsAt: null, note: '', stale: null };
      let rest = usedCell;
      let m = rest.match(/^([\d,]+) \/ ([\d,]+)(?: \(([\d.]+)%\))?/);
      if (m) { r.used = +m[1].replace(/,/g, ''); r.limit = +m[2].replace(/,/g, ''); r.pct = m[3] != null ? +m[3] : null; rest = rest.slice(m[0].length); }
      else if ((m = rest.match(/^([\d.]+)%/))) { r.pct = +m[1]; rest = rest.slice(m[0].length); }
      r.note = rest.replace(/^\s*·\s*/, '').trim();
      const st = r.note.match(/;?\s*stale ([\dhdm ]+) \(last Codex session\)/);
      if (st) { r.stale = durMs(st[1]) / 1000; r.note = r.note.replace(st[0], '').trim(); }
      if (/^reset$/.test(resets)) r.resetsAt = asOf;
      else if (resets && resets !== '—') {
        const rel = durMs(resets.split('(')[0]);
        if (rel) {
          r.resetsAt = asOf + rel; // "3d 15h" is rounded; snap to the exact "(Sun 09:00)" clock time
          const abs = resets.match(/\((?:\w+ )?(\d{1,2}):(\d{2})\)/);
          if (abs) {
            const d = new Date(r.resetsAt); d.setHours(+abs[1], +abs[2], 0, 0);
            let t = +d; if (t - r.resetsAt > 432e5) t -= 864e5; else if (r.resetsAt - t > 432e5) t += 864e5;
            r.resetsAt = t;
          }
        }
      }
      rows.push(r);
    });
    return rows.length ? { asOf, rows } : null;
  }

  const parse = (raw) => fromJson(raw) || fromTable(raw);

  // ── model ───────────────────────────────────────────────────────────────
  const WORDS = { '5h session': 'This session', '5h': '5-hour window', '7d': 'This week', '7d all models': 'This week · all models',
    'credits (month)': 'This month', 'extra usage (month)': 'Extra usage this month' };
  const words = (w) => WORDS[w] || (/^7d (.+)/.test(w) ? 'This week · ' + w.slice(3) : w);

  function services(rows) {
    const out = [];
    rows.forEach((r) => {
      let s = out.find((x) => x.key === r.service);
      if (!s) {
        const [name, ...rest] = r.service.split(' · ');
        // older replies still say "Astra" (internal name) for Codex
        s = { key: r.service, name, detail: rest.join(' · ').replace(/\s*\((.+)\)$/, ' · $1').replace(/\bAstra\b/g, 'Codex'), rows: [] };
        out.push(s);
      }
      s.rows.push(r);
    });
    return out;
  }

  const svcIcon = (n) => (/claude/i.test(n) ? 'SparklesIcon' : /chatgpt|codex|openai/i.test(n) ? 'AiBrain01Icon'
    : /higgsfield/i.test(n) ? 'Video01Icon' : /elevenlabs/i.test(n) ? 'VoiceIcon' : 'Analytics01Icon');

  // ── time words ──────────────────────────────────────────────────────────
  function rel(ms) {
    const s = Math.max(0, Math.round(ms / 1000));
    const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
    if (d) return h ? `${d}d ${h}h` : `${d}d`;
    if (h) return m ? `${h}h ${m}m` : `${h}h`;
    return `${Math.max(1, m)}m`;
  }
  function hm(t) {
    const d = new Date(t);
    let h = d.getHours();
    const ap = h < 12 ? 'am' : 'pm';
    h = h % 12 || 12;
    return `${h}:${String(d.getMinutes()).padStart(2, '0')}${ap}`;
  }
  function clock(t) {
    const d = new Date(t), now = new Date();
    const day = (x) => Math.floor((new Date(x.getFullYear(), x.getMonth(), x.getDate()) - 0) / 864e5);
    const diff = day(d) - day(now);
    if (diff === 0) return hm(t);
    if (diff === 1) return `tomorrow ${hm(t)}`;
    const wd = d.toLocaleDateString('en-GB', { weekday: 'short' });
    if (diff < 7) return `${wd} ${hm(t)}`;
    return `${wd} ${d.getDate()} ${d.toLocaleDateString('en-GB', { month: 'short' })}`;
  }
  const resetWords = (t) => (t == null ? '' : t <= Date.now() ? 'Resets now' : `Resets in ${rel(t - Date.now())} · ${clock(t)}`);

  // ── render ──────────────────────────────────────────────────────────────
  const level = (p) => (p >= 90 ? 'crit' : p >= 70 ? 'warn' : 'ok');

  function rowHtml(r) {
    const note = r.note || '';
    // not connected / unavailable: a quiet line with the fix
    if (r.window === '—' || (r.pct == null && r.used == null && !/credits left/.test(note))) {
      const slow = /timed out|TimeoutExpired/i.test(note);
      let fix = note.match(/`([^`]+)`/);
      if (slow || (fix && /^\/limit/.test(fix[1]))) fix = null; // the ↻ button is the retry
      let msg = note.replace(/\s*—?\s*(fix|run):?\s*`[^`]+`.*$/i, '').replace(/^\s*—\s*/, '').trim() || 'Unavailable';
      if (slow) msg = 'Didn’t answer in time · tap ↻ to check again';
      else if (/^not connected/i.test(msg)) msg = 'Not connected';
      const nice = msg.charAt(0).toUpperCase() + msg.slice(1);
      return `<div class="hx-lim-off">${icon(slow ? 'Clock01Icon' : 'Plug01Icon', 15)}<span><span class="hx-lim-off-t">${esc(nice)}</span>${fix
        ? `<span class="hx-lim-off-fix">Fix: <code>${esc(fix[1])}</code></span>` : ''}</span></div>`;
    }
    // credits balance
    const cl = note.match(/(\d[\d,]*(?:\.\d+)?) credits left/);  // decimals too (Higgsfield reports 11.75); shown as whole credits
    if (cl && r.pct == null) {
      return `<div class="hx-lim-credits"><span class="hx-lim-big">${esc(cl[1].replace(/\.\d+$/, ''))}</span><span class="hx-lim-unit">credits left</span></div>`;
    }
    const binding = /\bbinding\b/i.test(note);
    const reached = /limit reached/i.test(note);
    const extra = note.split(/\s*[;·]\s*/).filter((x) => x && !/^binding$/i.test(x) && !/^limit reached$/i.test(x));
    const p = r.pct != null ? Math.max(0, Math.min(100, Number(r.pct))) : (r.used != null && r.limit ? (100 * r.used) / r.limit : 0);
    const lv = reached ? 'crit' : level(p);
    const label = words(r.window);
    const sub = [];
    if (r.used != null && r.limit) sub.push(`${num(r.used)} of ${num(r.limit)}${/credits/.test(r.window) ? ' credits' : ''}`);
    const rw = resetWords(r.resetsAt);
    if (rw) sub.push(sub.length ? rw.charAt(0).toLowerCase() + rw.slice(1) : rw);
    if (r.stale != null) sub.push(`as of your last Codex session, ${rel(r.stale * 1000)} ago`);
    extra.forEach((x) => sub.push(x));
    const chip = reached ? '<span class="hx-lim-chip is-crit">Limit reached</span>'
      : binding ? '<span class="hx-lim-chip" title="Of this service’s limits, this is the one you’ll hit first">Closest to its limit</span>' : '';
    return `<div class="hx-lim-row${binding ? ' is-binding' : ''}" data-level="${lv}">
      <div class="hx-lim-line"><span class="hx-lim-lbl">${esc(label)}</span>${chip}<span class="hx-lim-pct"><b>${esc(pctTxt(p))}</b> used</span></div>
      <div class="hx-lim-bar" role="meter" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(p)}" aria-label="${esc(label)}: ${esc(pctTxt(p))} used"><i style="width:${p > 0 ? Math.max(p, 1.5) : 0}%"></i></div>
      ${sub.length ? `<div class="hx-lim-sub">${esc(sub.join(' · '))}</div>` : ''}
    </div>`;
  }

  function cardHtml(model, busy) {
    const svcs = services(model.rows).map((s) => `
      <section class="hx-lim-card" aria-label="${esc(s.key)}">
        <header class="hx-lim-svc"><span class="hx-lim-ic">${icon(svcIcon(s.name), 16)}</span>
          <span class="hx-lim-nm">${esc(s.name)}${s.detail ? `<span class="hx-lim-dt">${esc(s.detail)}</span>` : ''}</span></header>
        ${s.rows.map(rowHtml).join('')}
      </section>`).join('');
    return `<div class="hx-lim-head">
        <span class="hx-lim-title">Usage limits</span>
        <span class="hx-lim-meta">Updated ${esc(clock(model.asOf))}</span>
        <button type="button" class="hx-lim-refresh${busy ? ' is-busy' : ''}" aria-label="Check again" title="Check again"${busy ? ' disabled' : ''}>${icon('RefreshIcon', 15)}</button>
      </div>
      <div class="hx-lim-grid">${svcs}</div>`;
  }

  // ── wiring ──────────────────────────────────────────────────────────────
  const fresh = new Map(); // raw text -> refreshed model (survives upstream re-renders)

  function paint(card) {
    const html = cardHtml(card.__model, card.__busy);
    if (card.__html !== html) { card.innerHTML = html; card.__html = html; }
  }

  async function refresh(card) {
    if (card.__busy) return;
    card.__busy = true; paint(card);
    try {
      const body = JSON.stringify({ command: '/limit refresh' });
      const d = typeof window.api === 'function'
        ? await window.api('/api/commands/exec', { method: 'POST', body })
        : null;
      const m = d && parse(String(d.output || ''));
      if (m) { card.__model = m; fresh.set(card.__raw, m); }
    } catch (_) { /* keep the old numbers */ }
    card.__busy = false; paint(card);
  }

  function scan() {
    document.querySelectorAll('#messages .msg-row:not([data-role="user"]) [data-raw-text]').forEach((seg) => {
      const raw = seg.getAttribute('data-raw-text') || '';
      if (raw.indexOf('```' + FENCE) < 0 && raw.indexOf('Usage limits') < 0) return;
      const body = seg.querySelector(':scope > .msg-body') || seg.querySelector('.msg-body');
      if (!body) return;
      let card = body.querySelector(':scope > .hx-lim');
      if (card && card.__raw === raw) { paint(card); return; }
      const model = fresh.get(raw) || parse(raw);
      if (!model) return;
      if (!card) {
        card = document.createElement('div');
        card.className = 'hx-lim';
        card.setAttribute('role', 'group');
        card.setAttribute('aria-label', 'Usage limits');
        card.addEventListener('click', (e) => { if (e.target.closest('.hx-lim-refresh')) refresh(card); });
        body.prepend(card);
      }
      card.__raw = raw; card.__model = model;
      body.classList.add('hx-lim-host');
      paint(card);
    });
  }

  let queued = false;
  const mo = new MutationObserver((muts) => {
    if (muts.every((m) => { const t = m.target.nodeType === 1 ? m.target : m.target.parentElement; return t && t.closest('.hx-lim'); })) return;
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => { queued = false; scan(); });
  });
  function start() {
    const msgs = document.getElementById('messages');
    if (msgs) mo.observe(msgs, { childList: true, subtree: true });
    scan();
    setInterval(() => { if (!document.hidden) scan(); }, 30000); // keep "resets in" honest
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();

  // ── /limit feedback ─────────────────────────────────────────────────────
  // Upstream runs plugin commands (POST /api/commands/exec) before it renders anything, so /limit
  // looked frozen for a second or two. Show the user's message and a skeleton card right away; the
  // real reply replaces both when upstream re-renders.
  function skeleton() {
    const inner = document.getElementById('msgInner') || document.getElementById('messages');
    if (!inner) return null;
    const el = document.createElement('div');
    el.className = 'hx-lim-skel';
    el.setAttribute('role', 'status');
    el.setAttribute('aria-label', 'Checking usage limits');
    const card = '<div class="hx-lim-card"><div class="hx-lim-svc"><span class="hx-lim-ic"></span><span class="hx-sk hx-sk-t"></span></div>'
      + '<span class="hx-sk"></span><span class="hx-sk hx-sk-bar"></span><span class="hx-sk hx-sk-s"></span></div>';
    el.innerHTML = `<div class="hx-lim"><div class="hx-lim-head"><span class="hx-lim-title">Usage limits</span>`
      + `<span class="hx-lim-meta">Checking your accounts…</span></div><div class="hx-lim-grid">${card}${card}</div></div>`;
    inner.appendChild(el);
    if (typeof window.scrollToBottom === 'function') window.scrollToBottom();
    return el;
  }
  function wrapExec() {
    const orig = window.executeAgentPluginCommand;
    if (typeof orig !== 'function' || orig.__hxLim) return;
    const wrapped = async function (text, meta) {
      if (!/^\/limits?\b/i.test(String(text || '').trim())) return orig.apply(this, arguments);
      let sk = null;
      try {
        if (typeof window.renderMessages === 'function') window.renderMessages();  // the user's /limit bubble
        const box = document.getElementById('msg');
        if (box) { box.value = ''; if (typeof window.autoResize === 'function') window.autoResize(); }
        sk = skeleton();
      } catch (_) { /* feedback only */ }
      try { return await orig.apply(this, arguments); } finally { if (sk) sk.remove(); }
    };
    wrapped.__hxLim = true;
    window.executeAgentPluginCommand = wrapped;
  }
  // upstream's commands.js is deferred: wrap once it has run
  wrapExec();
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', wrapExec);
  window.addEventListener('load', wrapExec);
})();
