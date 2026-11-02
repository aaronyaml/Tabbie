/* Hermes — worker narration + attached images, under each reply's Agents block (plain JS, no build).
 *  1. Narration: the short lines a worker (Claude Code / Codex) writes between tool calls
 *     ("Let me check the CSS first…") appear live, typed in as they arrive. While the run is live the
 *     latest three show (older ones fold into "N earlier"); once it settles they fold into one toggle.
 *  2. Images: screenshots / renders the worker attached mid-run (an `ATTACH: <path>` line) show as
 *     thumbnails as soon as they're made; tap opens the full size.
 * Data: the cc-agents sidecar payload (cc-agents.js fetches it and exposes window.hxAgentsData), so it
 * survives a reload. Images come from GET sidecar/shot (the request's own copy, validated server-side).
 */
(() => {
  'use strict';
  if (window.__hxNarr) return;
  window.__hxNarr = true;

  const API = 'api/extensions/cc-agents/sidecar/';
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const icon = (n, s) => (typeof window.hxIcon === 'function' ? window.hxIcon(n, s) : '');
  const reduced = () => { try { return window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (_) { return false; } };
  const LIVE_SHOWN = 6;

  const openKeys = new Set();   // blocks whose narration the user expanded
  const typing = new Map();     // line key -> {t0, dur}
  const seen = new Set();       // line keys already shown (no re-typing after a re-render)
  let raf = 0;

  const lineKey = (rid, n) => `${rid}|${n.at}|${String(n.text).slice(0, 24)}`;
  const shotUrl = (rid, id, size) => `${API}shot?id=${encodeURIComponent(rid)}&img=${encodeURIComponent(id)}&size=${size}`;

  function requestsFor(el, data) {
    const rids = String(el.dataset.rids || '').split(',').filter(Boolean);
    return (data.requests || []).filter((r) => rids.includes(r.id));
  }

  function lineHtml(k, text, cls) {
    const t = typing.get(k);
    const shownText = t ? text.slice(0, Math.max(1, Math.round(text.length * Math.min(1, (performance.now() - t.t0) / t.dur)))) : text;
    return `<div class="hx-narr-line${cls}"${t ? ` data-ty="${esc(k)}"` : ''} data-full="${esc(text)}">${esc(shownText)}</div>`;
  }

  // Consecutive calls of one read-only tool fold into one row: "Read 3 files", "Searched 4 times".
  const FOLD = { Read: ['Reading', 'Read', 'file', 'files'], Grep: ['Searching', 'Searched', 'time', 'times'],
    Glob: ['Finding files', 'Found files', 'time', 'times'], WebFetch: ['Fetching', 'Fetched', 'page', 'pages'] };
  function timeline(lines) {
    const out = [];
    for (const l of lines) {
      const n = l.n;
      if ((n.kind || 'text') === 'text') { out.push({ kind: 'text', k: l.k, text: n.text }); continue; }
      const prev = out[out.length - 1];
      if (prev && prev.kind === 'op' && prev.tool === n.tool && FOLD[n.tool]) { prev.items.push(n); continue; }
      out.push({ kind: 'op', k: l.k, tool: n.tool, items: [n] });
    }
    for (const o of out) {
      if (o.kind !== 'op') continue;
      const st = o.items.some((x) => x.status === 'running') ? 'running' : (o.items.some((x) => x.status === 'failed') ? 'failed' : 'done');
      o.status = st;
      const f = FOLD[o.tool];
      if (o.items.length > 1 && f) {
        const n = o.items.length;
        o.label = `${st === 'running' ? f[0] : f[1]} ${n} ${f[3]}`;
        o.title = o.items.map((x) => x.done || x.text).join('\n');
      } else {
        const x = o.items[0];
        o.label = st === 'running' ? x.text : (x.done || x.text);
        o.title = o.label;
      }
    }
    return out;
  }

  // One small icon per kind of step (its colour is the status); the label drops "Ran:" (the icon says it).
  const OP_ICON = { Bash: 'ComputerTerminal01Icon', command_execution: 'ComputerTerminal01Icon', BashOutput: 'ComputerTerminal01Icon',
    Read: 'File01Icon', Edit: 'FileEditIcon', MultiEdit: 'FileEditIcon', Write: 'FileEditIcon', NotebookEdit: 'FileEditIcon',
    file_change: 'FileEditIcon', Grep: 'Search01Icon', Glob: 'Search01Icon', WebSearch: 'Search01Icon', web_search: 'Search01Icon',
    WebFetch: 'Globe02Icon', Agent: 'UserMultiple02Icon', Task: 'UserMultiple02Icon', collab_tool_call: 'UserMultiple02Icon',
    TodoWrite: 'CheckListIcon', Skill: 'Wrench01Icon', mcp_tool_call: 'Wrench01Icon' };
  const SHOT_RX = /screenshot|\.(png|jpe?g|webp|gif)\b|\bimage\b|render/i;
  function opIcon(it) {
    if (it.status === 'failed') return 'Alert02Icon';
    if (SHOT_RX.test(it.label)) return 'Image01Icon';
    return OP_ICON[it.tool] || 'Wrench01Icon';
  }
  function stepHtml(it, latest) {
    if (it.kind === 'text') return lineHtml(it.k, it.text, latest ? ' is-latest' : '');
    const label = String(it.label).replace(/^(Ran|Running):\s*/, '');
    return `<div class="hx-tl-op" data-status="${esc(it.status)}" title="${esc(it.title)}"><span class="hx-tl-ic" aria-hidden="true">${icon(opIcon(it), 13)}</span><span class="hx-tl-lb">${esc(label)}</span></div>`;
  }

  function build(el, reqs) {
    // live: the Agents block says so, or the request is still executing (its status is the source of truth)
    const live = el.classList.contains('is-live') || reqs.some((r) => r.status === 'executing');
    const key = el.dataset.key || '';
    const now = Date.now() / 1000;
    const lines = [];
    const imgs = [];
    const rejected = [];
    let tasks = 0;
    for (const r of reqs) {
      for (const x of r.attach_rejected || []) rejected.push(x);
      for (const n of r.narration || []) lines.push({ rid: r.id, n, k: lineKey(r.id, n) });
      for (const i of r.images || []) imgs.push({ rid: r.id, i });
      tasks += (r.agents_list || []).filter((a) => a.parent && a.status === 'running').length;
    }
    lines.sort((a, b) => (a.n.at || 0) - (b.n.at || 0));
    imgs.sort((a, b) => (a.i.at || 0) - (b.i.at || 0));
    // a line is typed in when it's new to this page and fresh (not on a reload of an old run)
    for (const l of lines) {
      if (seen.has(l.k)) continue;
      seen.add(l.k);
      if ((l.n.kind || 'text') === 'text' && live && !reduced() && now - (l.n.at || 0) < 8) {
        typing.set(l.k, { t0: performance.now(), dur: Math.min(1400, Math.max(320, l.n.text.length * 22)) });
      }
    }
    const steps = timeline(lines);
    const open = openKeys.has(key);
    let html = '';
    if (steps.length) {
      const said = steps.filter((x) => x.kind === 'text').length;
      const did = steps.length - said;
      if (live) {
        const hidden = open ? 0 : Math.max(0, steps.length - LIVE_SHOWN);
        const shown = steps.slice(hidden);
        const lastText = shown.map((x) => x.kind).lastIndexOf('text');
        html += `<div class="hx-narr-lines is-live">${hidden ? `<button type="button" class="hx-narr-more" data-narr="open">${hidden} earlier step${hidden === 1 ? '' : 's'}</button>` : ''}`
          + shown.map((x, i) => stepHtml(x, i === lastText)).join('')
          + (open && steps.length > LIVE_SHOWN ? '<button type="button" class="hx-narr-more" data-narr="close">Show fewer</button>' : '')
          + '</div>';
      } else {
        const parts = [said ? `${said} update${said === 1 ? '' : 's'}` : '', did ? `${did} step${did === 1 ? '' : 's'}` : ''].filter(Boolean).join(' · ');
        html += `<button type="button" class="hx-narr-toggle" data-narr="${open ? 'close' : 'open'}" aria-expanded="${open}">
            <span>${parts}</span><span class="hx-narr-caret">${icon('ArrowDown01Icon', 12)}</span></button>`
          + (open ? `<div class="hx-narr-lines">${steps.map((x) => stepHtml(x, false)).join('')}</div>` : '');
      }
    }
    if (imgs.length) {
      html += `<div class="hx-narr-shots">${imgs.map(({ rid, i }) => `<button type="button" class="hx-narr-shot" data-rid="${esc(rid)}" data-img="${esc(i.id)}" data-name="${esc(i.name)}" aria-label="Open ${esc(i.name)}" title="${esc(i.name)}">
          <img src="${esc(shotUrl(rid, i.id, 'thumb'))}" alt="${esc(i.name)}" loading="lazy" decoding="async"></button>`).join('')}</div>`;
    }
    if (rejected.length) {
      const why = [...new Set(rejected.map((x) => x.reason).filter(Boolean))].join(', ');
      html += `<div class="hx-narr-rej" title="${esc(rejected.map((x) => `${x.name}: ${x.reason}`).join('\n'))}">${icon('Alert02Icon', 13)}<span>${rejected.length} image${rejected.length === 1 ? '' : 's'} couldn't be shown${why ? ` (${esc(why)})` : ''}</span></div>`;
    }
    return { html, live, open, tasks, steps: steps.length };
  }

  // "Working… · 3 running tasks" (subagents + background tasks still running), next to the live status
  // word; and while the timeline has steps, the turn's generic "Running a tool" / "Thinking" rows hide.
  function markTurn(el, live, tasks, steps) {
    const row = el.closest('.msg-row, .assistant-turn') || el.parentElement;
    if (!row) return;
    row.classList.toggle('hx-tl-on', live && steps > 0);
    // the live status line under the reply ("Assembling… (6m 44s)") when there is one, else the group label
    const ls = live ? document.querySelector('#messages .hx-ls-line') : null;
    const lsMeta = ls && (ls.querySelector('.hx-ls-meta') || ls.querySelector('.hx-ls-word'));
    const label = lsMeta || (live ? row.querySelector('[data-live-tool-worklog-group="1"] .tool-worklog-label, [data-live-tool-call-group="1"] .tool-call-group-label, [data-tool-worklog-running="1"] .tool-worklog-label, .tool-call-group-label, .tool-worklog-label') : null);
    let tag = lsMeta ? ls.querySelector('.hx-tasks') : row.querySelector('.hx-tasks');
    row.querySelectorAll('.hx-tasks').forEach((t) => { if (t !== tag) t.remove(); });
    if (!label || !tasks) { if (tag) tag.remove(); return; }
    const txt = `· ${tasks} running task${tasks === 1 ? '' : 's'}`;
    if (!tag) { tag = document.createElement('span'); tag.className = 'hx-tasks'; }
    if (tag.previousElementSibling !== label && tag.parentNode !== label) label.after(tag);
    if (tag.textContent !== txt) tag.textContent = txt;
  }

  function render() {
    const data = typeof window.hxAgentsData === 'function' ? window.hxAgentsData() : null;
    const blocks = document.querySelectorAll('#messages .hx-agents[data-rids]');
    const keep = new Set();
    for (const el of blocks) {
      const reqs = data ? requestsFor(el, data) : [];
      const { html, live, open, tasks, steps } = build(el, reqs);
      markTurn(el, live, tasks, steps);
      let box = el.nextElementSibling && el.nextElementSibling.classList.contains('hx-narr') ? el.nextElementSibling : null;
      if (!html) { if (box) box.remove(); continue; }
      if (!box) {
        box = document.querySelector(`#messages .hx-narr[data-key="${CSS.escape(el.dataset.key || '')}"]`) || document.createElement('div');
        box.className = 'hx-narr';
        box.addEventListener('click', onClick);
        el.after(box);
      }
      keep.add(box);
      box.dataset.key = el.dataset.key || '';
      box.classList.toggle('is-live', live);
      box.classList.toggle('is-open', open);
      // typing nodes change text every frame: compare against the settled html
      const sig = html.replace(/ data-ty="[^"]*"/g, '').replace(/data-full="([^"]*)">[^<]*</g, 'data-full="$1"><');
      if (box.__sig !== sig) { box.innerHTML = html; box.__sig = sig; }
    }
    document.querySelectorAll('#messages .hx-narr').forEach((b) => { if (!keep.has(b)) b.remove(); });
    if (typing.size && !raf) raf = requestAnimationFrame(tick);
  }

  function tick() {
    raf = 0;
    const t = performance.now();
    for (const [k, v] of typing) {
      const p = Math.min(1, (t - v.t0) / v.dur);
      document.querySelectorAll(`#messages .hx-narr-line[data-ty="${CSS.escape(k)}"]`).forEach((node) => {
        const full = node.dataset.full || '';
        node.textContent = full.slice(0, Math.max(1, Math.round(full.length * p)));
        if (p >= 1) node.removeAttribute('data-ty');
      });
      if (p >= 1) typing.delete(k);
    }
    if (typing.size) raf = requestAnimationFrame(tick);
  }

  function onClick(e) {
    const box = e.currentTarget;
    const n = e.target.closest('[data-narr]');
    if (n) {
      const k = box.dataset.key || '';
      n.dataset.narr === 'open' ? openKeys.add(k) : openKeys.delete(k);
      render();
      return;
    }
  }

  // ── full-size viewer: every image in this chat, swipe / arrow keys to move between them ──
  let viewer = null, backTo = null, shots = [], at = 0;
  function closeShot() {
    if (!viewer) return;
    viewer.remove(); viewer = null; shots = [];
    document.removeEventListener('keydown', onKey, true);
    document.documentElement.classList.remove('hx-shot-lock');
    if (backTo && document.body.contains(backTo)) backTo.focus({ preventScroll: true });
    backTo = null;
  }
  function onKey(e) {
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeShot(); }
    else if (e.key === 'ArrowRight') { e.preventDefault(); go(at + 1); }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); go(at - 1); }
  }
  // The chat's images in reading order (only what this chat shows, never other chats).
  const chatShots = () => [...document.querySelectorAll('#messages .hx-narr-shot')]
    .map((b) => ({ rid: b.dataset.rid, id: b.dataset.img, name: b.dataset.name || '' }));
  function slide(k) {
    const sh = shots[k];
    const fig = document.createElement('figure');
    fig.className = 'hx-shot-fig';
    fig.innerHTML = `<img class="hx-shot-img" alt="${esc(sh.name)}" src="${esc(shotUrl(sh.rid, sh.id, 'thumb'))}" draggable="false">`;
    const img = fig.firstChild, full = new Image();
    full.onload = () => { if (img.isConnected) { img.src = full.src; img.classList.add('is-full'); } };
    full.src = shotUrl(sh.rid, sh.id, 'full');
    return fig;
  }
  function go(k, dir) {
    if (!viewer || k < 0 || k >= shots.length) { if (viewer) bounce(k < 0 ? 1 : -1); return; }
    const track = viewer.querySelector('.hx-shot-track');
    while (track.children.length > 1) track.firstElementChild.remove(); // a slide still leaving: drop it now
    const next = slide(k), prev = track.firstElementChild;
    const d = dir || (k > at ? 1 : -1);
    at = k;
    if (prev && !reduced()) {
      next.style.transform = `translateX(${d * 100}%)`;
      track.appendChild(next);
      void next.offsetWidth; // commit the start position, then slide (no frame wait, so it can't stick)
      prev.style.transition = next.style.transition = 'transform .32s cubic-bezier(.32,.72,0,1)';
      prev.style.transform = `translateX(${-d * 100}%)`;
      next.style.transform = 'translateX(0)';
      setTimeout(() => prev.remove(), 340);
    } else { track.replaceChildren(next); }
    if (viewer.__resetZoom) viewer.__resetZoom();
    viewer.querySelector('.hx-shot-cap').textContent = shots[k].name;
    viewer.querySelector('.hx-shot-n').textContent = shots.length > 1 ? `${k + 1} / ${shots.length}` : '';
    viewer.querySelector('.hx-shot-prev').disabled = k === 0;
    viewer.querySelector('.hx-shot-next').disabled = k === shots.length - 1;
    viewer.setAttribute('aria-label', shots[k].name || 'Image');
  }
  function bounce(d) {
    const f = viewer && viewer.querySelector('.hx-shot-fig');
    if (!f || reduced()) return;
    f.style.transition = 'transform .12s ease-out'; f.style.transform = `translateX(${d * 24}px)`;
    setTimeout(() => { f.style.transform = ''; }, 120);
  }
  function openShot(rid, id, name, from) {
    closeShot();
    backTo = from || null;
    shots = chatShots();
    at = Math.max(0, shots.findIndex((x) => x.rid === rid && x.id === id));
    if (!shots.length) shots = [{ rid, id, name }];
    viewer = document.createElement('div');
    viewer.className = 'hx-shot-box';
    viewer.setAttribute('role', 'dialog');
    viewer.setAttribute('aria-modal', 'true');
    const multi = shots.length > 1;
    viewer.innerHTML = `<div class="hx-shot-scrim"></div>
      <div class="hx-shot-track"></div>
      <div class="hx-shot-foot"><span class="hx-shot-cap"></span><span class="hx-shot-n"></span></div>
      <button type="button" class="hx-shot-nav hx-shot-prev" aria-label="Previous image"${multi ? '' : ' hidden'}>${icon('ArrowLeft01Icon', 20) || '&lsaquo;'}</button>
      <button type="button" class="hx-shot-nav hx-shot-next" aria-label="Next image"${multi ? '' : ' hidden'}>${icon('ArrowRight01Icon', 20) || '&rsaquo;'}</button>
      <button type="button" class="hx-shot-close" aria-label="Close">${icon('Cancel01Icon', 18) || '&times;'}</button>`;
    viewer.querySelector('.hx-shot-prev').addEventListener('click', (e) => { e.stopPropagation(); go(at - 1); });
    viewer.querySelector('.hx-shot-next').addEventListener('click', (e) => { e.stopPropagation(); go(at + 1); });
    viewer.querySelector('.hx-shot-close').addEventListener('click', closeShot);
    viewer.addEventListener('click', (e) => { if (!moved && !e.target.closest('.hx-shot-img, .hx-shot-nav')) closeShot(); });
    // Gestures. At normal size: left/right pages (the image follows the finger), up/down drags it
    // away (the backdrop fades; far or fast enough closes). Zoom: pinch, double-tap / double-click,
    // ctrl+wheel or trackpad pinch; while zoomed one finger (or the mouse) pans instead.
    const Z = { s: 1, x: 0, y: 0 };
    const img = () => viewer && viewer.querySelector('.hx-shot-fig:last-child .hx-shot-img');
    const fig = () => viewer && viewer.querySelector('.hx-shot-fig:last-child');
    function clampPan() {
      const el = img(); if (!el) return;
      const mx = Math.max(0, (el.offsetWidth * Z.s - innerWidth) / 2 + 16), my = Math.max(0, (el.offsetHeight * Z.s - innerHeight) / 2 + 16);
      Z.x = Math.max(-mx, Math.min(mx, Z.x)); Z.y = Math.max(-my, Math.min(my, Z.y));
    }
    function applyZoom(anim) {
      const el = img(); if (!el) return;
      if (Z.s <= 1.01) { Z.s = 1; Z.x = 0; Z.y = 0; }
      clampPan();
      el.style.transition = anim && !reduced() ? 'transform .25s cubic-bezier(.32,.72,0,1)' : 'none';
      el.style.transform = Z.s === 1 ? '' : `translate(${Z.x}px, ${Z.y}px) scale(${Z.s})`;
      viewer.classList.toggle('is-zoomed', Z.s > 1);
    }
    // zoom to `to`, keeping the screen point (cx, cy) still
    function zoomAt(to, cx, cy, anim) {
      const el = img(); if (!el) return;
      to = Math.max(1, Math.min(5, to));
      const r = el.getBoundingClientRect(), ox = r.left + r.width / 2, oy = r.top + r.height / 2;
      const k = to / Z.s;
      Z.x += (cx - ox) * (1 - k); Z.y += (cy - oy) * (1 - k); Z.s = to;
      applyZoom(anim);
    }
    viewer.__resetZoom = () => { Z.s = 1; Z.x = 0; Z.y = 0; viewer.classList.remove('is-zoomed'); };
    let x0 = null, y0 = 0, t0 = 0, dx = 0, dy = 0, moved = false, horiz = null;
    let pinch = null, pan = null, lastTap = 0;
    // two quick taps on the image zoom in, or back out when zoomed
    function tap(e) {
      const now = performance.now(), t = e.changedTouches[0];
      if (now - lastTap < 300 && t && e.target.closest('.hx-shot-img')) { lastTap = 0; zoomAt(Z.s > 1 ? 1 : 2.5, t.clientX, t.clientY, true); }
      else lastTap = now;
    }
    const dist = (t) => Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY);
    viewer.addEventListener('touchstart', (e) => {
      moved = false;
      if (e.touches.length === 2) {
        x0 = null; pan = null;
        pinch = { d: dist(e.touches), s: Z.s, cx: (e.touches[0].clientX + e.touches[1].clientX) / 2, cy: (e.touches[0].clientY + e.touches[1].clientY) / 2 };
        return;
      }
      if (e.touches.length !== 1) return;
      const t = e.touches[0];
      if (Z.s > 1) { pan = { x: t.clientX, y: t.clientY, zx: Z.x, zy: Z.y }; return; }
      x0 = t.clientX; y0 = t.clientY; t0 = performance.now(); dx = 0; dy = 0; horiz = null;
    }, { passive: true });
    viewer.addEventListener('touchmove', (e) => {
      if (pinch && e.touches.length === 2) {
        e.preventDefault(); moved = true;
        zoomAt(pinch.s * dist(e.touches) / pinch.d, pinch.cx, pinch.cy, false);
        return;
      }
      if (pan && e.touches.length === 1) {
        e.preventDefault(); moved = true;
        Z.x = pan.zx + e.touches[0].clientX - pan.x; Z.y = pan.zy + e.touches[0].clientY - pan.y;
        applyZoom(false);
        return;
      }
      if (x0 === null) return;
      const ddx = e.touches[0].clientX - x0, ddy = e.touches[0].clientY - y0;
      if (horiz === null && (Math.abs(ddx) > 8 || Math.abs(ddy) > 8)) horiz = Math.abs(ddx) > Math.abs(ddy);
      if (horiz === null) return;
      e.preventDefault(); moved = true;
      const f = fig();
      if (!horiz) {
        dy = ddy;
        const sc = viewer.querySelector('.hx-shot-scrim');
        if (f) { f.style.transition = 'none'; f.style.transform = `translateY(${dy}px) scale(${1 - Math.min(Math.abs(dy) / 2000, 0.12)})`; }
        if (sc) { sc.style.transition = 'none'; sc.style.opacity = String(Math.max(0.15, 1 - Math.abs(dy) / 400)); }
        return;
      }
      const edge = (ddx > 0 && at === 0) || (ddx < 0 && at === shots.length - 1);
      dx = edge ? ddx / 3 : ddx;
      if (f) { f.style.transition = 'none'; f.style.transform = `translateX(${dx}px)`; }
    }, { passive: false });
    viewer.addEventListener('touchend', (e) => {
      if (pinch) { if (e.touches.length < 2) { pinch = null; applyZoom(true); } return; }
      if (pan) { const still = !moved; pan = null; if (still) tap(e); return; }
      if (x0 === null) return;
      const f = fig(), ms = Math.max(1, performance.now() - t0);
      x0 = null;
      setTimeout(() => { moved = false; }, 0);
      if (horiz === null) { tap(e); return; }
      if (horiz === false) {
        const sc = viewer.querySelector('.hx-shot-scrim');
        if (Math.abs(dy) > 120 || (Math.abs(dy) / ms > 0.7 && Math.abs(dy) > 80)) {
          const v = viewer;
          if (f) { f.style.transition = 'transform .22s ease-in'; f.style.transform = `translateY(${dy > 0 ? innerHeight : -innerHeight}px)`; }
          if (sc) { sc.style.transition = 'opacity .22s ease-in'; sc.style.opacity = '0'; }
          setTimeout(() => { if (viewer === v) closeShot(); }, 200);
        } else {
          if (f) { f.style.transition = 'transform .25s cubic-bezier(.32,.72,0,1)'; f.style.transform = ''; }
          if (sc) { sc.style.transition = 'opacity .25s'; sc.style.opacity = ''; }
        }
        return;
      }
      const fast = Math.abs(dx) / ms > 0.45;
      const k = dx < 0 ? at + 1 : at - 1;
      if ((Math.abs(dx) > innerWidth * 0.2 || (fast && Math.abs(dx) > 30)) && k >= 0 && k < shots.length) go(k);
      else if (f) { f.style.transition = 'transform .25s cubic-bezier(.32,.72,0,1)'; f.style.transform = ''; }
    });
    // desktop: double-click toggles zoom, ctrl+wheel / trackpad pinch zooms, drag pans while zoomed
    viewer.addEventListener('dblclick', (e) => { if (e.target.closest('.hx-shot-img')) zoomAt(Z.s > 1 ? 1 : 2.5, e.clientX, e.clientY, true); });
    viewer.addEventListener('wheel', (e) => {
      if (!e.ctrlKey && Z.s === 1) return;
      e.preventDefault();
      if (e.ctrlKey) zoomAt(Z.s * Math.exp(-e.deltaY / 100), e.clientX, e.clientY, false);
      else { Z.x -= e.deltaX; Z.y -= e.deltaY; applyZoom(false); }
    }, { passive: false });
    let mdrag = null;
    viewer.addEventListener('mousedown', (e) => { if (Z.s > 1 && e.target.closest('.hx-shot-img')) { mdrag = { x: e.clientX, y: e.clientY, zx: Z.x, zy: Z.y }; e.preventDefault(); } });
    addEventListener('mousemove', (e) => { if (!mdrag || !viewer) return; moved = true; Z.x = mdrag.zx + e.clientX - mdrag.x; Z.y = mdrag.zy + e.clientY - mdrag.y; applyZoom(false); });
    addEventListener('mouseup', () => { if (mdrag) { mdrag = null; setTimeout(() => { moved = false; }, 0); } });
    document.body.appendChild(viewer);
    document.documentElement.classList.add('hx-shot-lock');
    go(at);
    document.addEventListener('keydown', onKey, true);
    viewer.querySelector('.hx-shot-close').focus({ preventScroll: true });
  }
  // ── loop ─────────────────────────────────────────────────────────────────
  let queued = false;
  const later = () => { if (queued) return; queued = true; requestAnimationFrame(() => { queued = false; try { render(); } catch (e) { console.warn('cc-narration', e); } }); };
  window.addEventListener('hx-agents-data', later);
  const mo = new MutationObserver((muts) => {
    if (muts.every((m) => { const t = m.target.nodeType === 1 ? m.target : m.target.parentElement; return t && t.closest('.hx-narr'); })) return;
    later();
  });
  // Any image tile in the chat opens the viewer (one listener, survives re-renders).
  document.addEventListener('click', (e) => {
    const t = e.target.closest && e.target.closest('#messages .hx-narr-shot');
    if (t) { e.preventDefault(); openShot(t.dataset.rid, t.dataset.img, t.dataset.name, t); }
  });
  function start() {
    const msgs = document.getElementById('messages');
    if (msgs) mo.observe(msgs, { childList: true, subtree: true });
    else setTimeout(start, 500);
    later();
  }
  setInterval(later, 2000);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
