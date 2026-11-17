/* Tabbie — charts drawn right inside a reply (plain JS, no build step, no library).
 * A worker writes a fenced block with the language `chart` holding JSON, and it renders in place as an SVG
 * chart that follows the theme, with hover values, a legend and a table view:
 *
 *   ```chart
 *   {"type": "bar", "title": "Signups per week", "unit": "", "labels": ["W1","W2","W3"],
 *    "series": [{"name": "iOS", "data": [12, 18, 25]}, {"name": "Android", "data": [8, 11, 19]}]}
 *   ```
 *
 * type: bar | line | area | pie | donut. "data": [...] alone is a one-series shorthand. "stacked": true stacks bars
 * or areas. "colors": ["#hex", …] overrides the palette (e.g. brand colours). "unit" goes after values ("%", " ms"),
 * "prefix" before them ("£"). The raw block stays in the message (copy uses it) but is hidden.
 */
(() => {
  'use strict';
  if (window.__hxCharts) return;
  window.__hxCharts = true;

  const SEL = '.msg-body pre > code.language-chart';
  const NS = 'http://www.w3.org/2000/svg';
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  // dataviz reference palette, fixed order; each mode stepped for its surface (validated set)
  const LIGHT = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#6250d6', '#e34948'];
  const DARK = ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181', '#008300', '#9085e9', '#e66767'];
  const isDark = () => document.documentElement.classList.contains('dark')
    || document.documentElement.dataset.theme === 'dark';
  const HEX = /^#[0-9a-f]{3}(?:[0-9a-f]{3})?$/i;

  // ── spec ────────────────────────────────────────────────────────────────
  function parse(raw) {
    let d;
    try { d = JSON.parse(raw); } catch (_) { return null; }
    if (!d || typeof d !== 'object') return null;
    const type = String(d.type || 'bar').toLowerCase();
    if (!['bar', 'line', 'area', 'pie', 'donut'].includes(type)) return null;
    let series = Array.isArray(d.series) ? d.series : Array.isArray(d.data) ? [{ name: d.name || d.title || '', data: d.data }] : null;
    if (!series || !series.length) return null;
    series = series.slice(0, 8).map((s, i) => ({
      name: String((s && s.name) || `Series ${i + 1}`),
      data: (Array.isArray(s && s.data) ? s.data : []).map((v) => (v == null || v === '' || isNaN(+v) ? null : +v)),
      color: s && HEX.test(s.color || '') ? s.color : null,
    }));
    const n = Math.max(...series.map((s) => s.data.length));
    if (!n) return null;
    const labels = Array.from({ length: n }, (_, i) => String((Array.isArray(d.labels) && d.labels[i] != null) ? d.labels[i] : i + 1));
    const colors = Array.isArray(d.colors) ? d.colors.filter((c) => HEX.test(c)) : [];
    return {
      type, labels, series, title: d.title ? String(d.title) : '', note: d.note ? String(d.note) : '',
      unit: d.unit ? String(d.unit) : '', prefix: d.prefix ? String(d.prefix) : '',
      stacked: !!d.stacked && (type === 'bar' || type === 'area'), colors,
    };
  }

  const fmt = (spec, v) => {
    if (v == null) return '—';
    const a = Math.abs(v);
    const s = a >= 1e9 ? `${+(v / 1e9).toFixed(1)}B` : a >= 1e6 ? `${+(v / 1e6).toFixed(1)}M` : a >= 1e4 ? `${+(v / 1e3).toFixed(1)}k`
      : (+v.toFixed(2)).toLocaleString('en-GB');
    return `${spec.prefix}${s}${spec.unit}`;
  };
  const full = (spec, v) => (v == null ? '—' : `${spec.prefix}${(+v.toFixed(4)).toLocaleString('en-GB')}${spec.unit}`);
  function colorOf(spec, i) {
    return spec.series[i].color || spec.colors[i] || (isDark() ? DARK : LIGHT)[i % 8];
  }
  function ticks(lo, hi, want) {
    if (lo === hi) { hi = lo + 1; }
    const raw = (hi - lo) / want;
    const mag = 10 ** Math.floor(Math.log10(raw));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || 10 * mag;
    const a = Math.floor(lo / step) * step, b = Math.ceil(hi / step) * step;
    const out = [];
    for (let v = a; v <= b + step / 2; v += step) out.push(+v.toFixed(10));
    return out;
  }
  const el = (tag, attrs, parent) => {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  };

  let ctx = null; // tick labels are measured off-DOM (an SVG not yet in the page measures 0)
  function textW(t) {
    if (!ctx) { ctx = document.createElement('canvas').getContext('2d'); }
    ctx.font = `11px ${getComputedStyle(document.body).fontFamily || 'system-ui'}`;
    return ctx.measureText(t).width;
  }

  // ── drawing ─────────────────────────────────────────────────────────────
  function drawXY(fig, spec, W) {
    const H = W < 420 ? 200 : 240;
    const multi = spec.series.length > 1;
    const n = spec.labels.length;
    // value range
    let lo = 0, hi = 0;
    if (spec.stacked) {
      for (let i = 0; i < n; i++) {
        let p = 0, q = 0;
        spec.series.forEach((s) => { const v = s.data[i] || 0; if (v >= 0) p += v; else q += v; });
        hi = Math.max(hi, p); lo = Math.min(lo, q);
      }
    } else {
      const all = spec.series.flatMap((s) => s.data).filter((v) => v != null);
      hi = Math.max(0, ...all); lo = Math.min(0, ...all);
      if (spec.type === 'line' && all.length) { // a line needn't start at zero when the values sit far from it
        const mn = Math.min(...all), mx = Math.max(...all);
        if (mn > 0 && mn > (mx - mn) * 1.5) lo = mn - (mx - mn) * 0.15;
      }
    }
    const tk = ticks(lo, hi, H < 220 ? 4 : 5);
    lo = tk[0]; hi = tk[tk.length - 1];
    const svg = el('svg', { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: 'img', 'aria-label': spec.title || 'Chart' });
    const left = Math.max(...tk.map((t) => textW(fmt(spec, t))));
    const pad = { l: Math.ceil(left) + 10, r: 8, t: 10, b: 24 };
    const pw = W - pad.l - pad.r, ph = H - pad.t - pad.b;
    const y = (v) => pad.t + ph - ((v - lo) / (hi - lo)) * ph;
    // grid
    const grid = el('g', { class: 'hx-cht-grid' }, svg);
    tk.forEach((t) => {
      el('line', { x1: pad.l, x2: W - pad.r, y1: y(t), y2: y(t), class: t === 0 ? 'is-zero' : '' }, grid);
      const tx = el('text', { x: pad.l - 8, y: y(t) + 4, 'text-anchor': 'end', class: 'hx-cht-tick' }, grid);
      tx.textContent = fmt(spec, t);
    });
    // x labels, thinned to fit
    const band = pw / n;
    const every = Math.max(1, Math.ceil(n / Math.max(1, Math.floor(pw / 56))));
    const xc = (i) => (spec.type === 'bar' ? pad.l + band * (i + 0.5) : pad.l + (n === 1 ? pw / 2 : (pw * i) / (n - 1)));
    spec.labels.forEach((lab, i) => {
      if (i % every) return;
      const t = el('text', { x: xc(i), y: H - 6, 'text-anchor': spec.type === 'bar' || (i > 0 && i < n - 1) ? 'middle' : i ? 'end' : 'start', class: 'hx-cht-tick' }, svg);
      t.textContent = lab.length > 12 ? lab.slice(0, 11) + '…' : lab;
    });
    const marks = el('g', {}, svg);
    const hits = [];
    if (spec.type === 'bar') {
      const groups = spec.stacked ? 1 : spec.series.length;
      const inner = Math.min(band * 0.72, groups * 44);
      const bw = Math.max(2, (inner - (groups - 1) * 2) / groups);
      for (let i = 0; i < n; i++) {
        let up = 0, down = 0;
        spec.series.forEach((s, si) => {
          const v = s.data[i];
          if (v == null) return;
          let a, b;
          if (spec.stacked) { if (v >= 0) { a = up; b = up += v; } else { a = down; b = down += v; } }
          else { a = 0; b = v; }
          const x = xc(i) - inner / 2 + (spec.stacked ? 0 : si * (bw + 2));
          const top = y(Math.max(a, b)), bot = y(Math.min(a, b));
          const h = Math.max(1, bot - top - (spec.stacked && a !== 0 ? 2 : 0)); // 2px surface gap between stacked fills
          const r = Math.min(4, bw / 2, h);
          const pos = b >= a;
          const d = pos
            ? `M${x},${top + h}V${top + r}Q${x},${top} ${x + r},${top}H${x + bw - r}Q${x + bw},${top} ${x + bw},${top + r}V${top + h}Z`
            : `M${x},${top}V${top + h - r}Q${x},${top + h} ${x + r},${top + h}H${x + bw - r}Q${x + bw},${top + h} ${x + bw},${top + h - r}V${top}Z`;
          el('path', { d, fill: colorOf(spec, si), class: 'hx-cht-bar', 'data-i': i }, marks);
        });
        hits.push({ i, x: pad.l + band * i, w: band });
      }
    } else {
      const order = spec.stacked ? [...spec.series.keys()] : [...spec.series.keys()].reverse();
      const base = new Array(n).fill(0);
      const stackedTop = spec.series.map(() => new Array(n).fill(null));
      spec.series.forEach((s, si) => s.data.forEach((v, i) => {
        if (spec.stacked) { base[i] += v || 0; stackedTop[si][i] = base[i]; } else stackedTop[si][i] = v;
      }));
      order.forEach((si) => {
        const c = colorOf(spec, si);
        const pts = stackedTop[si].map((v, i) => (v == null ? null : [xc(i), y(v)]));
        const segs = [];
        let cur = [];
        pts.forEach((p) => { if (p) cur.push(p); else if (cur.length) { segs.push(cur); cur = []; } });
        if (cur.length) segs.push(cur);
        segs.forEach((sg) => {
          const line = sg.map((p, k) => `${k ? 'L' : 'M'}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join('');
          if (spec.type === 'area') {
            const below = spec.stacked && si > 0
              ? sg.map((p) => { const i = Math.round(spec.labels.length === 1 ? 0 : (p[0] - pad.l) / (pw / (n - 1))); return [p[0], y(stackedTop[si - 1][i] || 0)]; }).reverse()
              : [[sg[sg.length - 1][0], y(Math.max(lo, 0))], [sg[0][0], y(Math.max(lo, 0))]];
            el('path', { d: line + below.map((p) => `L${p[0].toFixed(1)},${p[1].toFixed(1)}`).join('') + 'Z', fill: c, 'fill-opacity': spec.stacked ? 0.78 : 0.22 }, marks);
          }
          el('path', { d: line, fill: 'none', stroke: c, 'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }, marks);
          if (sg.length === 1 || n <= 12) sg.forEach((p) => el('circle', { cx: p[0], cy: p[1], r: n <= 12 ? 3 : 4, fill: c, class: 'hx-cht-dot' }, marks));
        });
      });
      for (let i = 0; i < n; i++) {
        const w = n === 1 ? pw : pw / (n - 1);
        hits.push({ i, x: xc(i) - w / 2, w });
      }
      fig._cross = el('line', { y1: pad.t, y2: pad.t + ph, class: 'hx-cht-cross', visibility: 'hidden' }, svg);
    }
    // hover targets (bigger than the marks)
    const hg = el('g', { class: 'hx-cht-hits' }, svg);
    hits.forEach((h) => {
      const r = el('rect', { x: Math.max(pad.l, h.x), y: pad.t, width: Math.max(1, Math.min(h.w, W - pad.r - Math.max(pad.l, h.x))), height: ph, fill: 'transparent', 'data-i': h.i }, hg);
      r.addEventListener('pointerenter', () => hover(fig, spec, h.i, xc(h.i), W));
      r.addEventListener('pointerdown', () => hover(fig, spec, h.i, xc(h.i), W));
    });
    svg.addEventListener('pointerleave', () => unhover(fig));
    return svg;
  }

  function drawPie(fig, spec, W) {
    const s = spec.series[0];
    const vals = s.data.map((v) => Math.max(0, v || 0));
    const tot = vals.reduce((a, b) => a + b, 0) || 1;
    const H = 200, R = 84, cx = Math.min(W / 2, 100), cy = H / 2;
    const inner = spec.type === 'donut' ? R * 0.6 : 0;
    const svg = el('svg', { viewBox: `0 0 ${Math.min(W, 200)} ${H}`, width: Math.min(W, 200), height: H, role: 'img', 'aria-label': spec.title || 'Chart' });
    let a = -Math.PI / 2;
    const pt = (ang, r) => [cx + r * Math.cos(ang), cy + r * Math.sin(ang)];
    vals.forEach((v, i) => {
      if (!v) return;
      const b = a + (v / tot) * Math.PI * 2;
      const big = b - a > Math.PI ? 1 : 0;
      let d;
      if (v === tot) {
        d = `M${cx - R},${cy}A${R},${R} 0 1 1 ${cx + R},${cy}A${R},${R} 0 1 1 ${cx - R},${cy}Z` + (inner ? `M${cx - inner},${cy}A${inner},${inner} 0 1 0 ${cx + inner},${cy}A${inner},${inner} 0 1 0 ${cx - inner},${cy}Z` : '');
      } else {
        const [x1, y1] = pt(a, R), [x2, y2] = pt(b, R);
        if (inner) {
          const [x3, y3] = pt(b, inner), [x4, y4] = pt(a, inner);
          d = `M${x1},${y1}A${R},${R} 0 ${big} 1 ${x2},${y2}L${x3},${y3}A${inner},${inner} 0 ${big} 0 ${x4},${y4}Z`;
        } else d = `M${cx},${cy}L${x1},${y1}A${R},${R} 0 ${big} 1 ${x2},${y2}Z`;
      }
      const p = el('path', { d, fill: colorOf({ ...spec, series: spec.labels.map(() => ({})) }, i), class: 'hx-cht-slice', 'fill-rule': 'evenodd', 'data-i': i }, svg);
      const show = () => hoverPie(fig, spec, i, v / tot);
      p.addEventListener('pointerenter', show);
      p.addEventListener('pointerdown', show);
      a = b;
    });
    svg.addEventListener('pointerleave', () => unhover(fig));
    return svg;
  }

  function tip(fig, html, x) {
    const t = fig.querySelector('.hx-cht-tip');
    t.innerHTML = html;
    t.hidden = false;
    const plot = fig.querySelector('.hx-cht-plot');
    const w = t.offsetWidth, max = plot.clientWidth;
    t.style.left = `${Math.round(Math.min(Math.max(0, x - w / 2), max - w))}px`;
  }
  function hover(fig, spec, i, x, W) {
    const rows = spec.series.map((s, si) => ({ s, si, v: s.data[i] })).filter((r) => r.v != null);
    if (spec.type === 'line' || spec.type === 'area') { rows.reverse(); if (!spec.stacked) rows.sort((a, b) => b.v - a.v); }
    tip(fig, `<b>${esc(spec.labels[i])}</b>` + rows.map((r) => `<span class="hx-cht-tr"><i style="background:${colorOf(spec, r.si)}"></i>${spec.series.length > 1 ? `<span>${esc(r.s.name)}</span>` : ''}<em>${esc(full(spec, r.v))}</em></span>`).join(''), x);
    if (fig._cross) { fig._cross.setAttribute('x1', x); fig._cross.setAttribute('x2', x); fig._cross.setAttribute('visibility', 'visible'); }
    fig.querySelectorAll('.hx-cht-bar').forEach((b) => b.classList.toggle('is-dim', +b.dataset.i !== i));
  }
  function hoverPie(fig, spec, i, share) {
    tip(fig, `<span class="hx-cht-tr"><i style="background:${colorOf({ ...spec, series: spec.labels.map(() => ({})) }, i)}"></i><span>${esc(spec.labels[i])}</span><em>${esc(full(spec, spec.series[0].data[i]))} · ${Math.round(share * 1000) / 10}%</em></span>`, 100);
    fig.querySelectorAll('.hx-cht-slice').forEach((p) => p.classList.toggle('is-dim', +p.dataset.i !== i));
  }
  function unhover(fig) {
    const t = fig.querySelector('.hx-cht-tip');
    if (t) t.hidden = true;
    if (fig._cross) fig._cross.setAttribute('visibility', 'hidden');
    fig.querySelectorAll('.is-dim').forEach((b) => b.classList.remove('is-dim'));
  }

  function legend(spec) {
    const pie = spec.type === 'pie' || spec.type === 'donut';
    if (!pie && spec.series.length < 2) return '';
    const items = pie
      ? spec.labels.map((l, i) => [l, colorOf({ ...spec, series: spec.labels.map(() => ({})) }, i), full(spec, spec.series[0].data[i])])
      : spec.series.map((s, i) => [s.name, colorOf(spec, i), '']);
    return `<div class="hx-cht-legend${pie ? ' is-pie' : ''}">${items.map(([l, c, v]) => `<span><i style="background:${c}"></i>${esc(l)}${v ? `<em>${esc(v)}</em>` : ''}</span>`).join('')}</div>`;
  }

  function table(spec) {
    const pie = spec.type === 'pie' || spec.type === 'donut';
    const cols = pie ? [spec.series[0].name || 'Value'] : spec.series.map((s) => s.name);
    return `<div class="hx-cht-table"><table><thead><tr><th></th>${cols.map((c) => `<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${
      spec.labels.map((l, i) => `<tr><th>${esc(l)}</th>${(pie ? [spec.series[0]] : spec.series).map((s) => `<td>${esc(full(spec, s.data[i]))}</td>`).join('')}</tr>`).join('')
    }</tbody></table></div>`;
  }

  function render(fig) {
    const spec = fig._spec;
    const plot = fig.querySelector('.hx-cht-plot');
    const W = Math.max(220, Math.floor(plot.clientWidth || fig.clientWidth || 480));
    if (fig._w === W && fig._dark === isDark() && plot.querySelector('svg')) return;
    fig._w = W; fig._dark = isDark(); fig._cross = null;
    plot.querySelector('svg')?.remove();
    const pie = spec.type === 'pie' || spec.type === 'donut';
    plot.insertBefore(pie ? drawPie(fig, spec, W) : drawXY(fig, spec, W), plot.firstChild);
    const lg = fig.querySelector('.hx-cht-legend');
    if (lg) lg.outerHTML = legend(spec);
  }

  const ro = 'ResizeObserver' in window ? new ResizeObserver((list) => list.forEach((e) => { const f = e.target.closest('.hx-chart'); if (f) render(f); })) : null;

  function build(code) {
    const pre = code.parentElement;
    if (pre.dataset.hxChart) return;
    const spec = parse(code.textContent);
    if (!spec) { // not valid (yet): a streaming block finishes later and is looked at again
      const row = pre.closest('.msg-row');
      const live = row && (row.querySelector('[data-live-assistant="1"]') || (row.nextElementSibling && row.nextElementSibling.id === 'hxLiveStatus'));
      if (!live) pre.classList.add('hx-chart-bad'); // finished but unreadable: show it as plain code
      return;
    }
    pre.dataset.hxChart = '1';
    const head = pre.previousElementSibling && pre.previousElementSibling.classList.contains('pre-header') ? pre.previousElementSibling : null;
    const fig = document.createElement('figure');
    fig.className = 'hx-chart';
    fig._spec = spec;
    fig.innerHTML = `<figcaption>${spec.title ? `<span class="hx-cht-title">${esc(spec.title)}</span>` : '<span></span>'}
        <button type="button" class="hx-cht-toggle" aria-pressed="false">Table</button></figcaption>
      <div class="hx-cht-plot"><div class="hx-cht-tip" hidden></div></div>${legend(spec) || ''}
      ${spec.note ? `<p class="hx-cht-note">${esc(spec.note)}</p>` : ''}${table(spec)}`;
    (head || pre).parentNode.insertBefore(fig, head || pre);
    pre.classList.add('hx-chart-src');
    if (head) head.classList.add('hx-chart-src');
    const wrap = pre.parentElement && pre.parentElement.classList.contains('pre-wrap') ? pre.parentElement : null;
    if (wrap) wrap.classList.add('hx-chart-src');
    render(fig);
    if (ro) ro.observe(fig.querySelector('.hx-cht-plot'));
  }

  document.addEventListener('click', (e) => {
    const b = e.target.closest('.hx-cht-toggle');
    if (!b) return;
    const fig = b.closest('.hx-chart');
    const on = !fig.classList.contains('is-table');
    fig.classList.toggle('is-table', on);
    b.setAttribute('aria-pressed', String(on));
    b.textContent = on ? 'Chart' : 'Table';
    if (!on) render(fig);
  });

  let timer = 0;
  function sweep() {
    timer = 0;
    document.querySelectorAll(SEL).forEach(build);
    document.querySelectorAll('.hx-chart').forEach((f) => { if (f._dark !== isDark()) { f._w = 0; render(f); } });
  }
  const schedule = () => { if (!timer) timer = setTimeout(sweep, 30); };
  new MutationObserver((list) => {
    for (const m of list) {
      const t = m.target.nodeType === 1 ? m.target : m.target.parentElement;
      if (t && t.closest && t.closest('.hx-chart')) continue;
      schedule();
      return;
    }
  }).observe(document.body, { childList: true, subtree: true, characterData: true });
  new MutationObserver(schedule).observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'data-theme'] });
  schedule();

  window.hxCharts = { parse, sweep };
})();
