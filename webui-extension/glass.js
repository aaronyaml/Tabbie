/*!
 * Tabbie — liquid glass (plain JS, no build step). Styles live in glass.css.
 *
 * The refraction field is a port of the fragment shader in liquid-glass-js
 * (https://github.com/dashersw/liquid-glass-js) — Copyright (c) 2025 Armagan
 * Amcalar, MIT. Permission is hereby granted, free of charge, to any person
 * obtaining a copy of that software to deal in it without restriction,
 * provided this notice travels with it; it is provided "as is", without
 * warranty of any kind. Keep this header on any copy or port.
 * Ported from the liquid-glass skill's Angular directive (stormaref-skills/liquid-glass).
 *
 * What this file does:
 *  - bends the backdrop through the edge of a few floating surfaces (composer, Back, sidebar toggle, Agents
 *    pill): a displacement map baked per element size, used as backdrop-filter: url(#…). Chromium only
 *    (Safari parses it and paints nothing, so it's gated on engine); elsewhere glass.css's frosted
 *    fallback stays. Off on phones and with reduced motion/transparency.
 *  - Settings › Appearance › "Liquid glass" switch (on by default; localStorage "hx-glass" = "off").
 */
(() => {
  'use strict';
  if (window.__hxGlass) return;
  window.__hxGlass = true;

  const KEY = 'hx-glass';
  const root = document.documentElement;
  const isOn = () => { try { return localStorage.getItem(KEY) !== 'off'; } catch (_) { return true; } };
  root.classList.toggle('hx-glass', isOn());

  // ── refraction ──────────────────────────────────────────────────────────────────────────────────
  const PRESET = { edgeIntensity: 0.015, rimIntensity: 0.028, baseIntensity: 0.05, edgeDistance: 0.5, rimDistance: 1.7,
    baseDistance: 0.2, cornerBoost: 0.06, rippleEffect: 0.26, blurRadius: 2, warp: false };
  const SUPERSAMPLE = 2, MAX_MAP_EDGE = 1400, BLUR_STD_PER_RADIUS = 0.35;
  // [selector, config overrides]. The composer frosts more: what you type sits on it.
  const TARGETS = [
    ['#composerBox', { blurRadius: 14 }],
    ['.hx-back', {}],
    ['#hxSidebarToggle', {}],
    ['.hx-agents-pill', {}],
  ];
  const supported = (() => {
    const b = navigator.userAgentData && navigator.userAgentData.brands;
    if (b) return b.some((x) => /Chromium|Google Chrome|Microsoft Edge/i.test(x.brand));
    return /Chrome\//.test(navigator.userAgent);
  })();
  const calm = () => matchMedia('(max-width: 640px), (prefers-reduced-motion: reduce), (prefers-reduced-transparency: reduce)').matches;

  const filters = new Map();
  let defs = null, nextId = 0;
  const clampByte = (v) => Math.max(0, Math.min(255, Math.round(v)));
  const resolveRadius = (c, w, h) => { const v = parseFloat(c) || 0; return c.trim().endsWith('%') ? (v / 100) * Math.min(w, h) : v; };

  function ensureDefs() {
    if (!defs || !defs.isConnected) {
      const ns = 'http://www.w3.org/2000/svg';
      defs = document.createElementNS(ns, 'svg');
      defs.setAttribute('width', '0'); defs.setAttribute('height', '0'); defs.setAttribute('aria-hidden', 'true');
      defs.style.position = 'fixed'; defs.style.pointerEvents = 'none';
      defs.appendChild(document.createElementNS(ns, 'defs'));
      document.body.appendChild(defs);
    }
    return defs.querySelector('defs');
  }

  function buildMap(w, h, radius, pageW, pageH, cfg) {
    const ss = Math.max(0.25, Math.min(SUPERSAMPLE, MAX_MAP_EDGE / Math.max(w, h)));
    const bw = Math.max(1, Math.round(w * ss)), bh = Math.max(1, Math.round(h * ss));
    const r = radius * ss, minDim = Math.min(w, h);
    const dx = new Float32Array(bw * bh), dy = new Float32Array(bw * bh);
    let maxAbs = 0;
    for (let py = 0; py < bh; py++) {
      for (let px = 0; px < bw; px++) {
        const cx = (px + 0.5) / bw, cy = (py + 0.5) / bh;
        const tx = Math.abs(px + 0.5 - bw / 2) - (bw / 2 - r);
        const ty = Math.abs(py + 0.5 - bh / 2) - (bh / 2 - r);
        const outside = Math.hypot(Math.max(tx, 0), Math.max(ty, 0));
        const inside = Math.min(Math.max(tx, ty), 0);
        const distPx = Math.max(-(outside + inside - r), 0) / ss;
        const edgeFall = Math.exp(-distPx * cfg.edgeDistance);
        const rimFall = Math.exp(-distPx * cfg.rimDistance);
        const baseFall = 1 - Math.exp(-distPx * cfg.baseDistance);
        const total = (cfg.warp ? baseFall * cfg.baseIntensity : 0) + edgeFall * cfg.edgeIntensity + rimFall * cfg.rimIntensity;
        let nx = cx - 0.5, ny = cy - 0.5;
        const len = Math.hypot(nx, ny);
        if (len > 0) { nx /= len; ny /= len; }
        const cornerNorm = Math.max(Math.min(cx, 1 - cx), Math.min(cy, 1 - cy)) * minDim;
        const corner = Math.exp(-cornerNorm * 0.3) * cfg.cornerBoost;
        const ripple = Math.sin((distPx / minDim) * 25) * cfg.rippleEffect * rimFall;
        const fx = (nx * (total + corner) - ny * ripple) * pageW;
        const fy = (ny * (total + corner) + nx * ripple) * pageH;
        const i = py * bw + px;
        dx[i] = fx; dy[i] = fy;
        maxAbs = Math.max(maxAbs, Math.abs(fx), Math.abs(fy));
      }
    }
    const scale = Math.max(maxAbs * 2, 1e-4);
    const bias = scale * (128 / 255 - 0.5);  // feDisplacementMap's neutral is 0.5, not 128/255
    const canvas = document.createElement('canvas');
    canvas.width = bw; canvas.height = bh;
    const ctx = canvas.getContext('2d');
    const img = ctx.createImageData(bw, bh), d = img.data;
    for (let i = 0; i < bw * bh; i++) {
      d[i * 4] = clampByte(255 * (0.5 + (dx[i] - bias) / scale));
      d[i * 4 + 1] = clampByte(255 * (0.5 + (dy[i] - bias) / scale));
      d[i * 4 + 2] = 128; d[i * 4 + 3] = 255;
    }
    ctx.putImageData(img, 0, 0);
    return { url: canvas.toDataURL('image/png'), scale };
  }

  function acquire(key, w, h, radius, pageW, pageH, cfg) {
    let e = filters.get(key);
    if (e && e.node.isConnected) { e.refs++; return e; }
    if (e) filters.delete(key);
    const { url, scale } = buildMap(w, h, radius, pageW, pageH, cfg);
    const ns = 'http://www.w3.org/2000/svg';
    const id = `hx-glass-${nextId++}`;
    const f = document.createElementNS(ns, 'filter');
    f.setAttribute('id', id);
    const m = scale / 2 + 3 * cfg.blurRadius * BLUR_STD_PER_RADIUS;
    const mx = (m / w) * 100, my = (m / h) * 100;
    f.setAttribute('x', `${-mx}%`); f.setAttribute('y', `${-my}%`);
    f.setAttribute('width', `${100 + 2 * mx}%`); f.setAttribute('height', `${100 + 2 * my}%`);
    f.setAttribute('color-interpolation-filters', 'sRGB');
    const im = document.createElementNS(ns, 'feImage');
    im.setAttribute('href', url); im.setAttribute('x', '0'); im.setAttribute('y', '0');
    im.setAttribute('width', String(w)); im.setAttribute('height', String(h));
    im.setAttribute('preserveAspectRatio', 'none'); im.setAttribute('result', 'map');
    const dm = document.createElementNS(ns, 'feDisplacementMap');
    dm.setAttribute('in', 'SourceGraphic'); dm.setAttribute('in2', 'map'); dm.setAttribute('scale', String(scale));
    dm.setAttribute('xChannelSelector', 'R'); dm.setAttribute('yChannelSelector', 'G'); dm.setAttribute('result', 'displaced');
    const bl = document.createElementNS(ns, 'feGaussianBlur');
    bl.setAttribute('in', 'displaced'); bl.setAttribute('stdDeviation', String(cfg.blurRadius * BLUR_STD_PER_RADIUS));
    f.append(im, dm, bl);
    ensureDefs().appendChild(f);
    e = { id, node: f, refs: 1 };
    filters.set(key, e);
    return e;
  }
  function release(key) {
    const e = key && filters.get(key);
    if (e && --e.refs <= 0) { filters.delete(key); e.node.remove(); }
  }

  // one record per glass element: its observer, its filter key
  const live = new Map();
  function rebuild(el, rec) {
    if (!el.isConnected) return;
    const w = Math.round(el.offsetWidth), h = Math.round(el.offsetHeight);
    if (w < 2 || h < 2) return;
    const radius = Math.min(resolveRadius(getComputedStyle(el).borderTopLeftRadius, w, h), Math.min(w, h) / 2);
    const cfg = { ...PRESET, ...rec.cfg };
    const key = [w, h, radius, innerWidth, innerHeight, JSON.stringify(cfg)].join('|');
    if (key === rec.key) return;
    release(rec.key);
    const e = acquire(key, w, h, radius, innerWidth, innerHeight, cfg);
    rec.key = key;
    el.style.setProperty('backdrop-filter', `url(#${e.id})`, 'important');
    el.style.setProperty('-webkit-backdrop-filter', `url(#${e.id})`, 'important');
  }
  function attach(el, cfg) {
    if (live.has(el)) return;
    const rec = { cfg, key: null, raf: 0, ro: null };
    rec.ro = new ResizeObserver(() => { cancelAnimationFrame(rec.raf); rec.raf = requestAnimationFrame(() => rebuild(el, rec)); });
    rec.ro.observe(el);
    live.set(el, rec);
    rebuild(el, rec);  // now, not only on the observer's first frame (rAF stalls in hidden tabs)
  }
  function detach(el) {
    const rec = live.get(el);
    if (!rec) return;
    rec.ro.disconnect(); cancelAnimationFrame(rec.raf); release(rec.key);
    el.style.removeProperty('backdrop-filter'); el.style.removeProperty('-webkit-backdrop-filter');
    live.delete(el);
  }
  function scan() {
    const want = root.classList.contains('hx-glass') && supported && !calm();
    for (const el of [...live.keys()]) if (!want || !el.isConnected) detach(el);
    if (!want) return;
    for (const [sel, cfg] of TARGETS) document.querySelectorAll(sel).forEach((el) => attach(el, cfg));
  }
  let rt = 0;
  addEventListener('resize', () => { clearTimeout(rt); rt = setTimeout(() => { for (const [el, rec] of live) rebuild(el, rec); scan(); }, 180); }, { passive: true });

  // ── Settings › Appearance › Liquid glass ─────────────────────────────────────────────────────────
  function setOn(on) {
    try { if (on) localStorage.removeItem(KEY); else localStorage.setItem(KEY, 'off'); } catch (_) {}
    root.classList.toggle('hx-glass', on);
    scan();
    paintSwitch();
  }
  function paintSwitch() {
    const b = document.querySelector('.hx-glass-set button[role="switch"]');
    if (b) b.setAttribute('aria-checked', String(root.classList.contains('hx-glass')));
  }
  function settingsRow() {
    const pane = document.getElementById('settingsPaneAppearance');
    if (!pane || pane.querySelector('.hx-glass-set')) return;
    const anchor = document.getElementById('settingsFontSize');
    const field = anchor && anchor.closest('.settings-field');
    const row = document.createElement('div');
    row.className = 'settings-field hx-glass-set';
    row.innerHTML = `<label><span>Liquid glass</span><button type="button" role="switch" aria-checked="${root.classList.contains('hx-glass')}" aria-label="Liquid glass"><i class="hx-switch" aria-hidden="true"></i></button></label>
      <div class="hx-glass-desc">Frosted, light-bending glass on the composer, menus, cards and floating buttons. Turn off for solid surfaces.</div>`;
    row.querySelector('label').addEventListener('click', (e) => { e.preventDefault(); setOn(!root.classList.contains('hx-glass')); });
    if (field) field.after(row); else pane.appendChild(row);
  }

  function sync() { scan(); settingsRow(); }
  sync();
  if (window.hxDom) window.hxDom.on(sync); else setInterval(sync, 1500);
  matchMedia('(max-width: 640px)').addEventListener('change', scan);
  window.hxGlass = { on: () => setOn(true), off: () => setOn(false), isOn: () => root.classList.contains('hx-glass') };
})();
