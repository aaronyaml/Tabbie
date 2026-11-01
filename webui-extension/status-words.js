/* Hermes — activity status words (plain JS, no build step). Visual only.
 * Upstream labels a turn's activity "Processed 14m 39s" both while it runs (ticking) and when
 * it settles. Like Claude Code: while live, one "-ing" word for the whole reply ("Pondering...", dots
 * filling in . .. ... with a small bounce, no timer), with a small line under it showing the latest step from the live feed (Claude's status
 * lines, tool steps, "Still working: …" heartbeats). Once settled: "Completed – 14m 39s".
 * Labels are rewritten in place; upstream keeps ticking the original text, which we re-translate.
 */
(function () {
  'use strict';
  const WORDS = ['Thinking', 'Pondering', 'Crafting', 'Brewing', 'Tinkering', 'Wrangling', 'Composing',
    'Conjuring', 'Untangling', 'Synthesizing', 'Percolating', 'Mulling', 'Orchestrating', 'Assembling',
    'Sketching', 'Polishing', 'Noodling', 'Cooking', 'Weaving', 'Calibrating'];
  const DASH = ' – ';
  // Upstream texts we translate: "Processed 3m 2s", "Processed", "Running", "Thinking…", "Working".
  const UPSTREAM = /^(Processed|Running|Thinking…?|Working)(?:\s+(?:for\s+)?(.+))?$/;
  const LABELS = '.tool-call-group-label, .tool-worklog-label';
  const GROUPS = '.agent-activity-group, .tool-call-group';

  const chosen = new Map(); // reply (stream id) -> its one word; upstream rebuilds group nodes mid-reply

  function isLive(g) {
    return g.getAttribute('data-live-tool-worklog-group') === '1' || g.getAttribute('data-live-tool-call-group') === '1'
      || g.getAttribute('data-tool-worklog-running') === '1';
  }

  function word(g) {
    // One key per reply that exists before the stream id does: session + number of user turns.
    let key = '';
    try {
      const turns = (S.messages || []).filter((x) => x && x.role === 'user').length;
      key = `${S.session && S.session.session_id}:${turns}`;
    } catch (_) { key = (g && g.getAttribute('data-stream-id')) || ''; }
    if (!chosen.has(key)) {
      if (chosen.size > 50) chosen.clear();
      chosen.set(key, WORDS[Math.floor(Math.random() * WORDS.length)]);
    }
    return chosen.get(key);
  }

  function subline(g, text) {
    const summary = g.querySelector(':scope > .tool-call-group-summary') || g.firstElementChild;
    let el = g.querySelector(':scope > .hx-status-sub');
    if (!text) { if (el) el.remove(); return; }
    if (!el) {
      el = document.createElement('div');
      el.className = 'hx-status-sub';
      el.style.cssText = 'font-size:12.5px;line-height:1.35;opacity:.62;margin:2px 0 6px;'
        + 'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;transition:opacity .2s';
      if (summary) summary.after(el); else g.prepend(el);
    }
    if (el.textContent !== text) el.textContent = text;
  }

  function rewrite(label) {
    const g = label.closest(GROUPS);
    if (!g) return;
    // Remember upstream's text (it may be ours already after a rewrite).
    const raw = (label.textContent || '').trim();
    if (UPSTREAM.test(raw)) label.dataset.hxRaw = raw;
    const src = label.dataset.hxRaw;
    if (!src) return;
    const m = src.match(UPSTREAM);
    if (!m) return;
    const elapsed = (m[2] || g.getAttribute('data-active-turn-elapsed') || '').trim();
    const live = isLive(g);
    subline(g, '');
    // "Used N tools" (apple-skin) owns bare settled "Processed" labels with no duration.
    if (!live && !elapsed) return;
    // While live the -ing word lives in the bottom status line (like Claude Code); the group
    // header stays quiet.
    const want = live ? 'Working' : `Completed${DASH}${elapsed}`;
    if (label.textContent !== want) {
      label.textContent = want;
      label.setAttribute('data-sweep-label', want);
    }
  }

  // ── bottom status line: buddy, shimmering "-ing" word, bouncing dots, latest step ──
  const LIVE_MARKS = '[data-live-assistant="1"], [data-live-tool-call-group="1"], '
    + '[data-live-tool-worklog-group="1"], [data-tool-worklog-running="1"]';
  const loadingView = (inner) => !!inner.querySelector('.hx-skel') || (inner.textContent || '').trim() === 'Loading conversation...';
  function turnLive(inner) {
    let busy = false;
    try { busy = !!S.busy; } catch (_) {}
    return busy || !!inner.querySelector(LIVE_MARKS);
  }
  function latestStepIn(inner) {
    // Prefer what a running agent is doing right now; the thinking log can end on a stale
    // "Finished · 33m 59s" from an earlier run while a resumed run is still going.
    const live = inner.querySelectorAll('.hx-ag[data-status="running"] .hx-ag-step, .hx-ag[data-status="waiting"] .hx-ag-step');
    let raw = live.length ? (live[live.length - 1].textContent || '').trim() : '';
    if (!raw) {
      const bodies = inner.querySelectorAll('.thinking-card-body');
      const body = bodies[bodies.length - 1];
      if (!body) return '';
      const lines = (body.innerText || body.textContent || '').split('\n').map((l) => l.trim()).filter(Boolean);
      raw = (lines[lines.length - 1] || '').replace(/^Thinking:\s*/, '').replace(/^Still working:\s*/i, '');
      if (/^(finished|done|completed)\b/i.test(raw)) return ''; // a finished step isn't "now"
    }
    // Human-ish: drop raw file paths, pretty-print model ids ("claude-opus-5-5" -> "Opus 5.5").
    const line = raw
      .split(' · ').filter((seg) => !/(^|\s)(~|\/)[\w.-]+\//.test(seg)).join(' · ')
      .replace(/\bclaude-(opus|sonnet|haiku|fable)-(\d+)-(\d+)\b/gi, (_, m, a, b) => `${m[0].toUpperCase()}${m.slice(1)} ${a}.${b}`)
      .replace(/\b(\d+(?:\.\d+)?)s\b/g, (m, n) => (+n >= 60 ? fmtDur(+n) : m))  // "2039s" -> "33m 59s"
      .replace(/\s*·\s*$/, '');
    const out = line || raw; // a bare command with paths: show it rather than nothing
    return out.length > 90 ? out.slice(0, 89) + '…' : out;
  }
  // "(37s · ↓ ~2.7k tokens · still thinking with medium effort)", like Claude Code.
  const STILL_AFTER = 8;   // s of continuous thinking before "still thinking" + warm colour
  const THOUGHT_FOR = 3;   // s to show "thought for Ns" after thinking stops
  const THINKING = '.agent-activity-thinking[data-thinking-active="1"], .agent-activity-thinking[data-live-thinking="1"]';
  const turn = { key: '', start: 0, thinkStart: 0, thoughtFor: 0, thoughtAt: 0, toks: 0 };
  const fmtDur = (sec) => { sec = Math.max(0, Math.round(sec)); if (sec < 60) return `${sec}s`;
    const h = Math.floor(sec / 3600), m = Math.floor(sec / 60) % 60;
    return h ? `${h}h ${m}m` : `${m}m ${sec % 60}s`; };
  const fmtTok = (n) => (n < 1000 ? String(n) : `${(n / 1000).toFixed(1)}k`);
  function liveMeta(inner) {
    const now = Date.now() / 1000;
    let key = '';
    try { key = `${S.session && S.session.session_id}:${(S.messages || []).filter((x) => x && x.role === 'user').length}`; } catch (_) {}
    if (key !== turn.key) Object.assign(turn, { key, start: 0, thinkStart: 0, thoughtFor: 0, thoughtAt: 0, toks: 0 });
    // Turn start: upstream's own stamp (survives reloads mid-turn), else when we first saw it.
    let started = 0;
    const g = inner.querySelector('[data-turn-started-at]');
    if (g) started = Number(g.getAttribute('data-turn-started-at')) || 0;
    if (!started) { try { started = Number(S.session && S.session.pending_started_at) || 0; } catch (_) {} }
    if (!started) started = turn.start || (turn.start = now);
    const parts = [fmtDur(now - started)];
    // Output so far: upstream only reports usage when the turn ends, so estimate from the
    // streamed reply + reasoning text (~4 chars per token) and mark it "~".
    let chars = 0;
    inner.querySelectorAll('[data-live-assistant="1"], ' + THINKING).forEach((n) => { chars += (n.textContent || '').length; });
    // never count down when a finished reasoning block stops being "live"
    const toks = turn.toks = Math.max(turn.toks, Math.round(chars / 4));
    if (toks >= 50) parts.push(`↓ ~${fmtTok(toks)} tokens`);
    let effort = '';
    try { effort = (typeof _currentReasoningEffort === 'string' && _currentReasoningEffort) || ''; } catch (_) {}
    const withEffort = effort && effort !== 'none' ? ` with ${effort} effort` : '';
    const thinking = !!inner.querySelector(THINKING);
    let warm = false;
    if (thinking) {
      if (!turn.thinkStart) turn.thinkStart = now;
      const t = now - turn.thinkStart;
      warm = t >= STILL_AFTER;
      parts.push(`${warm ? 'still thinking' : 'thinking'}${withEffort}`);
    } else if (turn.thinkStart) {
      turn.thoughtFor = now - turn.thinkStart; turn.thoughtAt = now; turn.thinkStart = 0;
    }
    if (!thinking && turn.thoughtAt && now - turn.thoughtAt < THOUGHT_FOR) parts.push(`thought for ${fmtDur(turn.thoughtFor)}`);
    return { text: `(${parts.join(' · ')})`, warm };
  }

  // ── flight: live status buddy -> reply header, once, when a watched reply settles ──
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
  // Tabbie's bird; Zoetie's (motion.js) in motion sessions. One blob per clip, fetched once.
  const FLY_DIRS = { tabbie: `${(window.HX_LOCAL || {}).hermes || ''}/brand/avatar/idle/web`, zoetie: `${(window.HX_LOCAL || {}).hermes || ''}/brand/avatar/motion/web` };
  const flyWho = () => (document.documentElement.classList.contains('hx-mo') ? 'zoetie' : 'tabbie');
  const flyBlobs = {};
  let flyBlob = '';
  let flyStart = 0;
  function warmFly() {
    const who = flyWho();
    if (flyBlobs[who]) { flyBlob = flyBlobs[who]; return; }
    if (flyStart) return;
    // Page load can briefly look "live"; keep the clip out of the startup burst.
    if (performance.now() < 5000) { flyStart = 1; setTimeout(() => { flyStart = 0; warmFly(); }, 5000 - performance.now()); return; }
    flyStart = 1; flyBlob = 'pending';
    const ext = /Apple/.test(navigator.vendor || '') ? 'mov' : 'webm';
    fetch(`api/media?path=${encodeURIComponent(`${FLY_DIRS[who]}/fly.${ext}`)}&v=1`, { credentials: 'same-origin' })
      .then((r) => (r.ok ? r.blob() : Promise.reject()))
      .then((b) => { flyBlobs[who] = URL.createObjectURL(b); if (flyWho() === who) flyBlob = flyBlobs[who]; flyStart = 0; },
        () => { flyBlob = ''; flyStart = 0; });
  }
  const onScreen = (r) => r && r.width > 0 && r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth;
  // Catmull-Rom through waypoints -> smooth keyframes.
  function spline(pts, n) {
    const out = [];
    for (let i = 0; i < pts.length - 1; i++) {
      const p0 = pts[Math.max(0, i - 1)], p1 = pts[i], p2 = pts[i + 1], p3 = pts[Math.min(pts.length - 1, i + 2)];
      for (let k = 0; k < n; k++) {
        const t = k / n, t2 = t * t, t3 = t2 * t;
        const f = (a0, a1, a2, a3) => 0.5 * ((2 * a1) + (-a0 + a2) * t + (2 * a0 - 5 * a1 + 4 * a2 - a3) * t2 + (-a0 + 3 * a1 - 3 * a2 + a3) * t3);
        out.push([f(p0[0], p1[0], p2[0], p3[0]), f(p0[1], p1[1], p2[1], p3[1])]);
      }
    }
    out.push(pts[pts.length - 1]);
    return out;
  }

  // The buddy swings out left, climbs up the left side of the reply, arcs right and bumps
  // "Hermes" over: the header icon grows from nothing to its place, pushing the name right.
  // Last reply's header, re-found every frame: upstream re-renders the row right after it settles,
  // so anything measured at take-off goes stale.
  function header() {
    const rows = document.querySelectorAll('#msgInner .msg-row.assistant-turn');
    const row = rows[rows.length - 1];
    const head = row && row.querySelector('.msg-role');
    return { row, head, icon: head && head.querySelector('.role-icon'), name: head && head.querySelector('.msg-role-name') };
  }
  const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
  function along(path, u) {
    const k = Math.max(0, Math.min(1, u)) * (path.length - 1), i = Math.floor(k), f = k - i;
    const a = path[i], b = path[Math.min(path.length - 1, i + 1)];
    return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f];
  }

  function flyHome(from) {
    const h0 = header();
    if (!h0.icon || !h0.name || document.hidden || reducedMotion.matches || typeof Element.prototype.animate !== 'function') return;
    h0.row.classList.add('hx-flying'); // hide the icon first: the name's left edge is then the icon's spot
    const nm0 = h0.name.getBoundingClientRect();
    if (!onScreen(from) || !onScreen(nm0)) { h0.row.classList.remove('hx-flying'); return; } // not watching both ends
    const size = 20, gap = parseFloat(getComputedStyle(h0.head).columnGap) || 8;
    const dim = parseFloat(getComputedStyle(h0.head).opacity) || 1; // header is drawn at ~.8 opacity
    const nameInset = nm0.left - h0.head.getBoundingClientRect().left; // icon spot, relative to the header
    const f = document.createElement('div');
    f.id = 'hxFlyer';
    f.style.cssText = `left:${from.left}px;top:${from.top}px;width:${from.width}px;height:${from.height}px`;
    // Still buddy + flapping "bird" clip stacked; they crossfade at take-off and landing.
    const still = document.createElement('i');
    still.className = 'hx-fl-still';
    f.appendChild(still);
    let v = null;
    if (flyBlob && flyBlob !== 'pending') {
      v = document.createElement('video');
      v.muted = true; v.playsInline = true; v.loop = true; v.autoplay = true; v.src = flyBlob;
      v.setAttribute('muted', ''); v.setAttribute('playsinline', '');
      v.style.opacity = '0';
      f.appendChild(v); f.classList.add('hx-has-video'); v.play().catch(() => {});
    }
    document.body.appendChild(f);
    const sx = from.left + from.width / 2, sy = from.top + from.height / 2, endScale = size / from.width;
    // Where the icon will sit (viewport centre), live.
    const spot = (hd) => {
      const hr = hd.head.getBoundingClientRect(), nr = hd.name.getBoundingClientRect();
      return [hr.left + nameInset + size / 2, nr.top + nr.height / 2];
    };
    // start -> out to the left -> up the left side -> arc right over the header -> into the spot.
    const route = (ix, iy) => {
      const L = Math.max(10, Math.min(sx, ix) - 80), top = Math.max(14, iy - 46);
      return spline([[sx, sy], [sx - 30, sy + 4], [L, sy - 10], [L - 6, (sy + top) / 2], [L + 4, top + 10],
        [(L + ix) / 2 - 6, top], [ix - 30, iy - 8], [ix, iy]], 10);
    };
    const p0 = route(...spot(h0));
    const len = p0.reduce((acc, p, i) => (i ? acc + Math.hypot(p[0] - p0[i - 1][0], p[1] - p0[i - 1][1]) : 0), 0);
    const dur = Math.min(1900, Math.max(1100, len * 1.3));
    const PUSH = 0.8; // from here the icon slot opens and shoves the name over as the buddy arrives
    let pushedIcon = null, done = false;
    const t0 = performance.now();
    const finish = () => {
      if (done) return; done = true;
      const hd = header();
      if (hd.row) { hd.row.classList.remove('hx-flying', 'hx-landing'); }
      if (hd.icon) {
        hd.icon.style.opacity = ''; hd.icon.style.width = ''; hd.icon.style.marginRight = '';
        hd.icon.animate([{ transform: 'scale(1.14, .88)' }, { transform: 'scale(.95, 1.06)', offset: 0.55 }, { transform: 'scale(1)' }],
          { duration: 300, easing: 'ease-out' });
      }
      f.animate([{ opacity: dim }, { opacity: 0 }], { duration: 120, fill: 'forwards' }).onfinish = () => f.remove();
    };
    const step = (now) => {
      if (done) return;
      const hd = header();
      if (!hd.icon || !hd.name) { f.remove(); done = true; return; }
      hd.row.classList.add('hx-flying');
      const t = Math.max(0, Math.min(1, (now - t0) / dur)), u = ease(t);
      const [ix, iy] = spot(hd);
      const path = route(ix, iy);
      const [x, y] = along(path, u), [nx, ny] = along(path, u + 0.02), [px, py] = along(path, u - 0.02);
      const vx = nx - px, vy = ny - py;
      const face = vx < -1 ? -1 : 1; // face the way it's flying
      const tilt = t > 0.94 ? 0 : Math.max(-18, Math.min(18, Math.atan2(vy, Math.abs(vx) + 0.001) * 57.3 * 0.5));
      const sc = 1 + (endScale - 1) * u; // normal size throughout: buddy-size -> icon-size, no swelling
      if (v) { // morph: still -> bird over the first 15%, bird -> still over the last 15%
        const bird = Math.min(1, t / 0.15, (1 - t) / 0.15);
        v.style.opacity = String(bird); still.style.opacity = String(1 - bird);
      }
      f.style.transform = `translate(${x - sx}px, ${y - sy}px) rotate(${tilt * face}deg) scale(${sc * face}, ${sc})`;
      f.style.opacity = String(1 - (1 - dim) * u);
      if (t >= PUSH && pushedIcon !== hd.icon) {
        // Open the slot (invisible icon growing 0 -> 20px): the name slides right as the buddy lands.
        const fresh = !pushedIcon;
        pushedIcon = hd.icon;
        hd.row.classList.add('hx-landing');
        hd.icon.style.opacity = '0';
        if (fresh) {
          const ms = Math.max(120, dur * (1 - PUSH));
          hd.icon.animate([{ width: '0px', marginRight: `${-gap}px` }, { width: `${size}px`, marginRight: '0px' }],
            { duration: ms, easing: 'cubic-bezier(.3,1.4,.6,1)' });
          hd.name.animate([{ transform: 'translateX(0)' }, { transform: 'translateX(4px)', offset: 0.7 }, { transform: 'translateX(0)' }],
            { duration: ms + 160, easing: 'ease-out' });
        }
      }
      if (t < 1) requestAnimationFrame(step); else finish();
    };
    requestAnimationFrame(step);
    setTimeout(finish, dur + 1500); // backstop (background tab throttling etc.)
  }

  const flight = { liveKey: '', rect: null, rectAt: 0, offSince: 0, flown: new Set() };

  function bottomStatus() {
    const inner = document.getElementById('msgInner');
    if (!inner) return;
    let el = document.getElementById('hxLiveStatus');
    // Switching sessions: S.busy can still describe the previous chat while the new one loads.
    if (loadingView(inner)) { if (el) el.remove(); flight.liveKey = ''; flight.rect = null; return; }
    if (!turnLive(inner)) {
      // Settled (debounced: upstream can blink "not busy" mid-turn). Fly once per watched turn.
      const now = Date.now();
      if (!flight.offSince) flight.offSince = now;
      if (el && now - flight.offSince < 700) return;
      if (el) { const b = el.querySelector('.hx-ls-buddy'); if (b) { flight.rect = b.getBoundingClientRect(); flight.rectAt = now; } el.remove(); }
      if (flight.liveKey && !flight.flown.has(flight.liveKey)) {
        flight.flown.add(flight.liveKey);
        const from = flight.rect;
        if (from && now - flight.rectAt < 2500) flyHome(from);
      }
      flight.liveKey = '';
      return;
    }
    flight.offSince = 0;
    warmFly();
    if (!el) {
      el = document.createElement('div');
      el.id = 'hxLiveStatus';
      el.setAttribute('role', 'status');
      el.innerHTML = '<span class="hx-ls-buddy" aria-hidden="true"></span><div class="hx-ls-text">'
        + '<div class="hx-ls-line"><span class="hx-ls-word"></span><span class="hx-dots" aria-hidden="true">'
        + '<i>.</i><i>.</i><i>.</i></span><span class="hx-ls-meta"></span></div><div class="hx-ls-sub"></div></div>';
    }
    if (inner.lastElementChild !== el) inner.appendChild(el);
    // Flush under a bare "Working" line; breathing room under cards, text or an open group.
    const prev = el.previousElementSibling, last = prev && prev.querySelector('.assistant-turn-blocks > :last-child');
    const sum = last && last.querySelector(':scope > .tool-call-group-summary');
    const flush = !!(sum && last.getBoundingClientRect().bottom - sum.getBoundingClientRect().bottom < 4);
    el.classList.toggle('hx-ls-spaced', !flush);
    const w = word(null);
    const wEl = el.querySelector('.hx-ls-word');
    if (wEl.textContent !== w) wEl.textContent = w;
    const meta = liveMeta(inner);
    flight.liveKey = turn.key;
    const bud = el.querySelector('.hx-ls-buddy');
    if (bud) { flight.rect = bud.getBoundingClientRect(); flight.rectAt = Date.now(); }
    const mEl = el.querySelector('.hx-ls-meta');
    if (mEl.textContent !== meta.text) mEl.textContent = meta.text;
    el.classList.toggle('hx-ls-warm', meta.warm);
    const step = latestStepIn(inner);
    const sub = el.querySelector('.hx-ls-sub');
    if (sub.textContent !== step) sub.textContent = step;
  }

  function scan(root) {
    (root || document).querySelectorAll(LABELS).forEach(rewrite);
    bottomStatus();
  }

  const css = document.createElement('style');
  css.textContent = `
    .hx-dots { display: inline-flex; margin-left: 1px; }
    .hx-dots i { display: inline-block; font-style: normal; opacity: 0;
      animation: 1.5s cubic-bezier(.34,1.56,.64,1) infinite; will-change: transform, opacity; }
    .hx-dots i:nth-child(1) { animation-name: hx-dot1; }
    .hx-dots i:nth-child(2) { animation-name: hx-dot2; }
    .hx-dots i:nth-child(3) { animation-name: hx-dot3; }
    @keyframes hx-dot1 { 0% { opacity: 1; transform: translateY(0); } 6% { transform: translateY(-3px); }
      14%, 100% { opacity: 1; transform: translateY(0); } }
    @keyframes hx-dot2 { 0%, 32.9% { opacity: 0; transform: translateY(0); } 33% { opacity: 1; } 39% { transform: translateY(-3px); }
      47%, 100% { opacity: 1; transform: translateY(0); } }
    @keyframes hx-dot3 { 0%, 65.9% { opacity: 0; transform: translateY(0); } 66% { opacity: 1; } 72% { transform: translateY(-3px); }
      80%, 100% { opacity: 1; transform: translateY(0); } }
    #hxLiveStatus { display: flex; align-items: flex-start; gap: 10px; margin: 0 0 4px; padding: 2px 2px 4px;
      animation: hx-ls-in .25s ease-out; }
    .hx-ls-buddy { flex: 0 0 22px; height: 22px; margin-top: -1px;
      background: url("/extensions/brand/mascot-256.png?v=4") center / contain no-repeat;
      animation: hx-ls-bob 1.6s ease-in-out infinite; }
    .hx-ls-text { min-width: 0; flex: 1; }
    .hx-ls-line { font-size: 15px; font-weight: 600; letter-spacing: -0.01em; line-height: 20px; color: var(--hx-text-2, var(--muted)); }
    /* Claude-style glint: a brighter band sweeping across the word */
    .hx-ls-word { background: linear-gradient(100deg, var(--hx-ls-base) 0%, var(--hx-ls-base) 38%,
        var(--hx-ls-hi) 50%, var(--hx-ls-base) 62%, var(--hx-ls-base) 100%);
      background-size: 250% 100%; -webkit-background-clip: text; background-clip: text;
      -webkit-text-fill-color: transparent; color: transparent; animation: hx-glint 2.2s linear infinite; }
    @property --hx-ls-base { syntax: '<color>'; inherits: true; initial-value: #8a8a8e; }
    @property --hx-ls-hi { syntax: '<color>'; inherits: true; initial-value: #ffffff; }
    #hxLiveStatus { --hx-ls-base: var(--hx-text-2, #8a8a8e); --hx-ls-hi: var(--hx-text, #fff);
      transition: --hx-ls-base 1.6s ease, --hx-ls-hi 1.6s ease; }
    /* long thinking: drift to the wing's warm orange */
    #hxLiveStatus.hx-ls-spaced { margin-top: 12px; }
    #hxLiveStatus.hx-ls-warm { --hx-ls-base: #E9773A; --hx-ls-hi: #FFC28F; }
    .hx-ls-line .hx-dots { color: var(--hx-ls-base); }
    .hx-ls-meta { margin-left: 8px; font-size: 13px; font-weight: 500; letter-spacing: 0; color: var(--hx-text-2, var(--muted));
      opacity: .8; font-variant-numeric: tabular-nums; }
    .hx-ls-sub { font-size: 12.5px; line-height: 1.35; color: var(--hx-text-2, var(--muted)); opacity: .75; margin-top: 2px;
      white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .hx-ls-sub:empty { display: none; }
    /* One buddy per reply. While the reply is live the header avatar is hidden and the buddy animates
       in the bottom status line; when it settles the buddy flies back up beside "Hermes" (if you're
       watching) and stays there as a still image. */
    .msg-row.assistant-turn:has([data-live-assistant="1"], [data-live-tool-call-group="1"], [data-live-tool-worklog-group="1"], [data-tool-worklog-running="1"]) .msg-role .role-icon,
    .msg-row.assistant-turn:has(+ #hxLiveStatus) .msg-role .role-icon,
    .msg-row.assistant-turn.hx-flying .msg-role .role-icon { display: none !important; }
    .msg-row.assistant-turn.hx-flying.hx-landing .msg-role .role-icon { display: flex !important; }
    /* Status reads as part of the reply: no gap between the last block and it */
    .msg-row.assistant-turn:has(+ #hxLiveStatus) .assistant-turn-blocks > :last-child > .tool-call-group-summary + .tool-call-group-body:has(> *) { margin-top: 12px; }
    .msg-row.assistant-turn:has(+ #hxLiveStatus) { padding-bottom: 0 !important; margin-bottom: 0 !important; }
    .msg-row.assistant-turn:has(+ #hxLiveStatus) .assistant-turn-blocks > :last-child,
    .msg-row.assistant-turn:has(+ #hxLiveStatus) .assistant-turn-blocks > :last-child > .tool-call-group-summary { margin-bottom: 0 !important; }
    #hxFlyer { position: fixed; z-index: 9999; pointer-events: none; will-change: transform, opacity; transform-origin: 50% 50%;
      background: url("/extensions/brand/mascot-256.png?v=4") center / contain no-repeat; }
    #hxFlyer, #hxFlyer.hx-has-video { background: none; } /* drawn by .hx-fl-still + video */
    #hxFlyer .hx-fl-still { position: absolute; inset: 0; background: url("/extensions/brand/mascot-256.png?v=4") center / contain no-repeat; }
    #hxFlyer video { position: absolute; inset: -15%; width: 130%; height: 130%; object-fit: fill; } /* square clip; fill avoids Safari's half-size 300x150 layout */
    @keyframes hx-glint { from { background-position: 100% 0; } to { background-position: -50% 0; } }
    @keyframes hx-ls-bob { 0%, 100% { transform: translateY(0) rotate(-3deg); } 50% { transform: translateY(-2px) rotate(3deg); } }
    @keyframes hx-ls-in { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: none; } }
    @media (prefers-reduced-motion: reduce) { .hx-dots i { animation: none; opacity: 1; }
      .hx-ls-word, .hx-ls-buddy, #hxLiveStatus { animation: none; } }`;
  document.head.appendChild(css);

  let queued = false;
  const mo = new MutationObserver(() => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => { queued = false; scan(document.getElementById('messages')); });
  });

  function start() {
    const msgs = document.getElementById('messages');
    scan(msgs);
    setInterval(bottomStatus, 1000); // ticks the elapsed time; also clears the line when S.busy flips
    if (msgs) mo.observe(msgs, { childList: true, subtree: true, characterData: true,
      attributes: true, attributeFilter: ['data-live-tool-worklog-group', 'data-live-tool-call-group', 'data-tool-worklog-running'] });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
