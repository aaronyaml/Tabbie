/* Tabbie — the Insights page.
 * Upstream Insights only knows the Hermes hub's own sessions, counts activity in sessions and prices nothing
 * useful. Tabbie replaces its usage sections with one page built from the sidecar (GET /usage,
 * plugin/command-centre/usage_insights.py): every token on this Mac from Claude Code, Codex and the hub, for
 * Today / 7 / 30 / 90 days: API-equivalent cost (tokens x list price per model), tokens per day, weekday
 * and hour, cost by token type, and the period by model, project and chat. Upstream's System health, LLM Wiki
 * and Skill usage cards stay (restyled) under "System"; every other upstream section, the "Model health
 * comparison" and the sidebar's period picker are removed from the DOM.
 */
(() => {
  'use strict';
  if (window.__hxInsightsUsage) return;
  window.__hxInsightsUsage = true;

  const API = 'api/extensions/cc-agents/sidecar/usage';
  const STALE_MS = 60000;
  const WINDOWS = [['today', 'Today'], ['7d', '7 days'], ['30d', '30 days'], ['90d', '90 days']];
  const SPAN = { today: 14, '7d': 14, '30d': 30, '90d': 90 };   // days drawn in the daily chart
  const SOURCES = [['claude', 'Claude Code'], ['codex', 'Codex'], ['hub', 'Hub']];
  const KEEP = new Set(['systemHealthPanel', 'llmWikiStatusCard', 'skillUsageCard']);
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const tok = (n) => {
    n = n || 0;
    const f = (v, u) => (v >= 100 ? Math.round(v) : v >= 10 ? v.toFixed(1).replace(/\.0$/, '') : v.toFixed(2).replace(/\.?0+$/, '')) + u;
    return n >= 1e9 ? f(n / 1e9, 'B') : n >= 1e6 ? f(n / 1e6, 'M') : n >= 1e3 ? f(n / 1e3, 'K') : String(Math.round(n));
  };
  const usd = (n) => {
    n = n || 0;
    if (n >= 1000) return '$' + Math.round(n).toLocaleString('en-US');
    if (n >= 100) return '$' + n.toFixed(0);
    if (n > 0 && n < 0.01) return '<$0.01';
    return '$' + n.toFixed(2);
  };
  const pct = (a, b) => (b > 0 ? Math.round((100 * a) / b) : 0);
  const fmtDay = (s, o) => new Date(s + 'T12:00:00').toLocaleDateString(undefined, o || { day: 'numeric', month: 'short' });
  // "19:00" or "7 PM", whichever the locale uses
  const hourName = (h) => new Date(2000, 0, 1, h).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' }).replace(/:00(?=\s?[AP]M)/i, '');
  const hourRange = (h) => `${hourName(h)}–${hourName((h + 1) % 24)}`;

  let win = '7d';
  try { win = localStorage.getItem('hx-iu-win') || win; } catch (_) {}
  if (!WINDOWS.some(([k]) => k === win)) win = '7d';

  let data = null, at = 0, busy = false, retry = null, root = null, tip = null;

  async function load(force) {
    if (busy) return;
    if (!force && data && !data.scanning && !data.error && Date.now() - at < STALE_MS) return paint();
    busy = true;
    if (root) root.classList.add('is-loading');
    try {
      const r = await fetch(API + (force ? '?force=1' : ''), { credentials: 'same-origin' });
      if (!r.ok) throw new Error(String(r.status));
      data = await r.json();
      at = Date.now();
    } catch (_) {
      if (!data || data.error) data = { error: true };
    } finally { busy = false; }
    paint();
    clearTimeout(retry);
    if (data && data.scanning) retry = setTimeout(() => load(false), 4000);  // first scan still running
  }

  // ── charts: stacked columns (tokens), one per day / weekday / hour, one tooltip ─────────────────────
  function nice(max) {
    if (max <= 0) return 1;
    const p = Math.pow(10, Math.floor(Math.log10(max)));
    const m = max / p;
    return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 2.5 ? 2.5 : m <= 5 ? 5 : 10) * p;
  }

  // items: [{label, tip: html, parts: {claude, codex, hub} (tokens), dim}]; ticks: [[index, text]]
  function columns(items, ticks, cls) {
    const top = nice(Math.max(1, ...items.map((it) => SOURCES.reduce((a, [k]) => a + (it.parts[k] || 0), 0))));
    const cols = items.map((it, i) => {
      const segs = SOURCES.filter(([k]) => it.parts[k] > 0).map(([k]) =>
        `<i class="s-${k}" style="height:${Math.max(0.6, (100 * it.parts[k]) / top).toFixed(2)}%"></i>`).join('');
      return `<div class="hx-ins-col${it.dim ? ' dim' : ''}" tabindex="0" role="img" data-i="${i}" aria-label="${esc(it.aria)}"><span>${segs}</span></div>`;
    }).join('');
    const grid = [1, 0.5].map((f) => `<div class="hx-ins-grid" style="bottom:${f * 100}%"><span>${tok(top * f)}</span></div>`).join('');
    const axis = ticks.map(([i, t]) => `<span style="left:${((i + 0.5) / items.length) * 100}%">${esc(t)}</span>`).join('');
    return `<div class="hx-ins-plot ${cls || ''}" style="--n:${items.length}">${grid}<div class="hx-ins-cols">${cols}</div></div>
      <div class="hx-ins-axis">${axis}</div>`;
  }

  const srcTip = (title, parts, extra) => {
    const total = SOURCES.reduce((a, [k]) => a + (parts[k] || 0), 0);
    return `<b>${tok(total)} tokens</b><span class="t">${esc(title)}</span>` +
      SOURCES.filter(([k]) => parts[k] > 0).map(([k, n]) => `<span class="r"><i class="s-${k}"></i>${esc(n)}<em>${tok(parts[k])}</em></span>`).join('') +
      (extra || '');
  };

  function legend() {
    return `<div class="hx-ins-legend">${SOURCES.map(([k, n]) => `<span><i class="s-${k}"></i>${n}</span>`).join('')}</div>`;
  }

  // ── sections ────────────────────────────────────────────────────────────────────────────────────────
  function hero(d, w) {
    const src = w.by_source || {};
    const segs = SOURCES.filter(([k]) => (src[k] || {}).tokens > 0);
    const bar = `<div class="hx-ins-seg" role="img" aria-label="${esc(segs.map(([k, n]) => `${n} ${pct(src[k].tokens, w.tokens)}% of tokens`).join(', '))}">${segs.map(([k]) =>
      `<i class="s-${k}" style="flex-grow:${src[k].tokens}"></i>`).join('')}</div>`;
    const rows = SOURCES.map(([k, n]) => {
      const s = src[k] || { tokens: 0, cost: 0 };
      return `<div class="hx-ins-src"><i class="s-${k}"></i><span class="n">${n}</span><b>${usd(s.cost)}</b><span class="m">${tok(s.tokens)} tokens</span></div>`;
    }).join('');
    const label = WINDOWS.find(([k]) => k === win)[1];
    const cache = w.cr + w.cw5 + w.cw1;
    return `<section class="hx-ins-card hx-ins-hero">
      <div class="hx-ins-hero-main">
        <div class="hx-ins-kicker">API-equivalent cost · ${esc(label.toLowerCase())}</div>
        <div class="hx-ins-figure">${usd(w.cost)}</div>
        <p class="hx-ins-sub">What these tokens would cost at API list prices. They ran on your subscriptions, so none of this is billed.</p>
        ${bar}
        <div class="hx-ins-srcs">${rows}</div>
      </div>
      <div class="hx-ins-stats">
        <div class="hx-ins-stat"><span>Tokens</span><b>${tok(w.tokens)}</b><em>all kinds</em></div>
        <div class="hx-ins-stat"><span>Input</span><b>${tok(w.in)}</b><em>new, uncached</em></div>
        <div class="hx-ins-stat"><span>Output</span><b>${tok(w.out)}</b><em>incl. reasoning</em></div>
        <div class="hx-ins-stat"><span>Cache</span><b>${tok(cache)}</b><em>${pct(w.cr, cache)}% reads</em></div>
      </div>
    </section>`;
  }

  function daily(d) {
    const n = SPAN[win];
    const days = (d.by_day || []).slice(-n);
    const inWin = { today: 1, '7d': 7, '30d': 30, '90d': 90 }[win];
    const items = days.map((x, i) => {
      const parts = {}; SOURCES.forEach(([k]) => { parts[k] = (x[k] || {}).tokens || 0; });
      const title = fmtDay(x.day, { weekday: 'short', day: 'numeric', month: 'short' });
      return { parts, dim: i < days.length - inWin, aria: `${title}: ${tok(x.tokens)} tokens, ${usd(x.cost)}`,
        tip: srcTip(title, parts, `<span class="r f">${tok(x.in)} in · ${tok(x.out)} out · ${tok(x.cache)} cache<em>${usd(x.cost)}</em></span>`) };
    });
    const step = n <= 14 ? 3 : n <= 30 ? 7 : 15;
    const ticks = [];
    for (let i = days.length - 1; i >= 0; i -= step) ticks.unshift([i, i === days.length - 1 ? 'Today' : fmtDay(days[i].day)]);
    const table = `<details class="hx-ins-table"><summary>Show as table</summary><div class="hx-ins-tbl" role="table">
      <div class="hx-ins-tr th" role="row"><span>Day</span>${SOURCES.map(([, nm]) => `<span>${nm}</span>`).join('')}<span>Cost</span></div>
      ${days.slice().reverse().map((x) => `<div class="hx-ins-tr" role="row"><span>${esc(fmtDay(x.day, { weekday: 'short', day: 'numeric', month: 'short' }))}</span>${SOURCES.map(([k]) => `<span>${tok((x[k] || {}).tokens)}</span>`).join('')}<span>${usd(x.cost)}</span></div>`).join('')}
    </div></details>`;
    return { items, html: `<section class="hx-ins-card" data-chart="day">
      <div class="hx-ins-head"><h3>Tokens per day</h3>${legend()}</div>
      ${columns(items, ticks, 'tall')}${table}</section>` };
  }

  function activity(w) {
    const wd = (w.by_weekday || []).map((r) => ({ name: r.day, r }));
    const hr = (w.by_hour || []).map((r) => ({ name: hourRange(r.hour), r }));
    const mk = (list) => list.map(({ name, r }) => {
      const parts = { claude: r.claude || 0, codex: r.codex || 0, hub: r.hub || 0 };
      return { parts, aria: `${name}: ${tok(r.tokens)} tokens, ${usd(r.cost)}`,
        tip: srcTip(name, parts, `<span class="r f">${tok(r.in)} in · ${tok(r.out)} out<em>${usd(r.cost)}</em></span>`) };
    });
    const wdItems = mk(wd), hrItems = mk(hr);
    const peak = (list) => list.reduce((a, b) => (b.r.tokens > a.r.tokens ? b : a), list[0] || { r: { tokens: 0 } });
    const pw = peak(wd), ph = peak(hr);
    const note = (p) => (p && p.r.tokens > 0 ? `<span class="hx-ins-peak">Busiest: ${esc(p.name)}</span>` : '');
    return {
      wdItems, hrItems,
      html: `<div class="hx-ins-pair">
        <section class="hx-ins-card" data-chart="wd"><div class="hx-ins-head"><h3>Tokens by weekday</h3>${note(pw)}</div>
          ${columns(wdItems, wd.map((x, i) => [i, x.name]), 'short')}</section>
        <section class="hx-ins-card" data-chart="hr"><div class="hx-ins-head"><h3>Tokens by hour</h3>${note(ph)}</div>
          ${columns(hrItems, [0, 6, 12, 18].map((h) => [h, hourName(h)]), 'short')}</section>
      </div>`,
    };
  }

  function costParts(w) {
    const p = w.cost_parts || {};
    const rows = [['in', 'Input', w.in], ['out', 'Output', w.out], ['cr', 'Cache reads', w.cr], ['cw', 'Cache writes', w.cw5 + w.cw1]];
    const top = Math.max(0.0001, ...rows.map(([k]) => p[k] || 0));
    return `<section class="hx-ins-card"><div class="hx-ins-head"><h3>Cost by token type</h3></div>
      <div class="hx-ins-list">${rows.map(([k, n, t]) => `<div class="hx-ins-row">
        <div class="hx-ins-row-main"><span class="hx-ins-name">${n}</span><span class="hx-ins-cost">${usd(p[k])}</span></div>
        <div class="hx-ins-meter"><i style="width:${Math.max(p[k] > 0 ? 1 : 0, Math.round((100 * (p[k] || 0)) / top))}%"></i></div>
        <div class="hx-ins-row-sub">${tok(t)} tokens · ${pct(p[k] || 0, w.cost)}% of cost</div></div>`).join('')}</div></section>`;
  }

  function list(title, rows, detail) {
    if (!rows || !rows.length) return '';
    const top = Math.max(0.0001, ...rows.map((r) => r.cost));
    return `<section class="hx-ins-card"><div class="hx-ins-head"><h3>${esc(title)}</h3></div><div class="hx-ins-list">${rows.map((r) => `
      <div class="hx-ins-row">
        <div class="hx-ins-row-main"><span class="hx-ins-name" title="${esc(r.name)}">${esc(r.name)}${r.estimate ? '<sup title="Estimated price">*</sup>' : ''}${r.priced === false ? '<sup title="No price known for this model">?</sup>' : ''}</span>
          <span class="hx-ins-cost">${r.priced === false ? '–' : usd(r.cost)}</span></div>
        <div class="hx-ins-meter"><i style="width:${Math.max(1, Math.round((100 * r.cost) / top))}%"></i></div>
        <div class="hx-ins-row-sub">${detail(r)}</div>
      </div>`).join('')}</div></section>`;
  }

  const tokDetail = (r) => `${tok(r.tokens)} tokens · ${tok(r.in)} in · ${tok(r.out)} out`;
  const modelDetail = (r) => [`${tok(r.in)} in`, `${tok(r.out)} out`, r.cr && `${tok(r.cr)} cache read`,
    (r.cw5 + r.cw1) && `${tok(r.cw5 + r.cw1)} cache write`].filter(Boolean).join(' · ');

  function priceTable(d) {
    const n = (v) => (v ? '$' + (Math.round(v * 100) === v * 100 ? v.toFixed(2) : String(+v.toFixed(3))) : '–');
    const used = new Set(((d.windows || {})['90d'] || { by_model: [] }).by_model.map((m) => m.rate).filter(Boolean));
    const prices = (d.prices || []).slice().sort((a, b) => (used.has(b.id) - used.has(a.id)));
    const row = (p) => `<div class="hx-ins-tr${used.has(p.id) ? ' used' : ''}" role="row"><span>${esc(p.name)}${p.long ? `<sup title="Over ${tok(p.long_over)} tokens per request: ${n(p.long.input)} in, ${n(p.long.output)} out">†</sup>` : ''}</span><span>${n(p.input)}</span>
      <span>${n(p.output)}</span><span>${n(p.cache_read)}</span><span>${p.cache_write_short ? `${n(p.cache_write_short)} / ${n(p.cache_write_long)}` : '–'}</span></div>`;
    return `<details class="hx-ins-card hx-ins-prices"><summary><h3>Prices used</h3><span>US$ per million tokens · list prices checked ${esc(d.prices_checked || '')}</span></summary>
      <div class="hx-ins-tbl prices" role="table">
        <div class="hx-ins-tr th" role="row"><span>Model</span><span>Input</span><span>Output</span><span>Cache read</span><span>Cache write</span></div>
        ${prices.map(row).join('')}
      </div>
      <p>Models used in the last 90 days are listed first. Cache write: Anthropic 5-minute / 1-hour, OpenAI 30-minute / 12-hour (Codex doesn't report writes, so none are charged). † A higher long-context price applies to requests over the threshold; hover to see it. A newer version of a listed model is priced as the newest listed one and marked *.</p></details>`;
  }

  function notes(w) {
    const out = [];
    if (w.estimated_cost > 0) {
      const names = w.by_model.filter((m) => m.estimate).map((m) => m.name).join(', ');
      out.push(`${usd(w.estimated_cost)} uses estimated prices (*): ${names} ${names.includes(',') ? 'have' : 'has'} no list price yet, so the newest listed model of the same family is used.`);
    }
    if (w.unpriced_tokens > 0) {
      const names = w.by_model.filter((m) => m.priced === false).map((m) => m.name).join(', ');
      out.push(`No price is known for ${names} (?): its ${tok(w.unpriced_tokens)} tokens aren't in the cost.`);
    }
    return out.length ? `<p class="hx-ins-note">${esc(out.join(' '))}</p>` : '';
  }

  function controls(d) {
    const btns = WINDOWS.map(([k, label]) => `<button type="button" class="hx-ins-win${k === win ? ' on' : ''}" data-win="${k}" aria-pressed="${k === win}">${label}</button>`).join('');
    let when = '';
    if (d && d.scanned_at) {
      const m = Math.round((Date.now() / 1000 - d.scanned_at) / 60);
      when = m < 1 ? 'Updated just now' : `Updated ${m} min ago`;
    }
    if (d && d.scanning) when = 'Reading history…';
    return `<div class="hx-ins-bar"><div class="hx-ins-seg-ctl" role="group" aria-label="Period">${btns}</div>
      <span class="hx-ins-when">${esc(when)}</span>
      <button type="button" class="hx-ins-refresh" aria-label="Refresh" title="Refresh"><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/></svg></button></div>`;
  }

  let charts = {};
  function html(d) {
    charts = {};
    const bar = controls(d);
    if (!d || d.error) return bar + '<section class="hx-ins-card hx-ins-empty"><h3>Usage couldn\'t be loaded</h3><p>Check that Tabbie\'s helper (the sidecar) is running, then refresh.</p></section>';
    if (!d.windows) return bar + '<section class="hx-ins-card hx-ins-empty hx-ins-wait"><h3>Reading Claude Code, Codex and hub history…</h3><p>The first scan takes a few seconds.</p></section>';
    const w = d.windows[win];
    if (!w || !w.by_hour) return bar + '<section class="hx-ins-card hx-ins-empty"><h3>Insights needs the updated helper</h3><p>The new usage data (hub tokens, 90 days, tokens by hour) loads after Tabbie\'s helper restarts.</p></section>';
    const label = WINDOWS.find(([k]) => k === win)[1];
    const dd = daily(d);
    const act = activity(w);
    charts = { day: dd.items, wd: act.wdItems, hr: act.hrItems };
    const empty = !w.tokens;
    return `${bar}${hero(d, w)}${notes(w)}${dd.html}
      ${empty ? `<section class="hx-ins-card hx-ins-empty"><p>No model use ${win === 'today' ? 'today' : 'in the last ' + esc(label)}.</p></section>` : `${act.html}
      <div class="hx-ins-pair">${costParts(w)}${list('By model', w.by_model, modelDetail)}</div>
      <div class="hx-ins-pair">${list('By project', w.by_project, tokDetail)}${list('By chat', w.by_chat, tokDetail)}</div>`}
      ${priceTable(d)}`;
  }

  // The kept upstream cards (System health, LLM Wiki, Skill usage) go after Tabbie's page, under "System".
  function tidy(host) {
    for (const el of [...host.children]) {
      if (el === root || el.classList.contains('hx-ins-sys-h') || KEEP.has(el.id)) continue;
      el.remove();
    }
    let h = host.querySelector(':scope > .hx-ins-sys-h');
    if (host.firstElementChild !== root) host.prepend(root);
    if (![...KEEP].some((id) => document.getElementById(id))) { if (h) h.remove(); return tidyChrome(host); }
    if (!h) { h = document.createElement('h2'); h.className = 'hx-ins-sys-h'; h.textContent = 'System'; }
    if (root.nextElementSibling !== h) root.after(h);
    let prev = h;
    for (const id of ['systemHealthPanel', 'skillUsageCard', 'llmWikiStatusCard']) {
      const el = document.getElementById(id);
      if (el && el.parentElement === host) { if (prev.nextElementSibling !== el) prev.after(el); prev = el; }
    }
    tidyChrome(host);
  }

  function tidyChrome(host) {
    // the sidebar's period picker drove upstream's sections, which are gone: Tabbie's control is on the page
    document.querySelectorAll('#panelInsights .insights-range-bar').forEach((el) => el.remove());
    const sub = host.querySelector('#systemHealthPanel');  // upstream assumes a VPS; Tabbie runs on this computer
    if (sub) for (const n of sub.querySelectorAll('*')) if (n.childElementCount === 0 && n.textContent === 'Current VPS resource usage') n.textContent = 'This computer, right now';
    const t = document.querySelector('#mainInsights .main-view-title');
    if (t && t.textContent !== 'Insights') { t.textContent = 'Insights'; t.removeAttribute('data-i18n'); }
  }

  function paint() {
    const host = document.getElementById('insightsContent');
    if (!host) return;
    if (!root) {
      root = document.createElement('div');
      root.className = 'hx-ins';
      root.setAttribute('aria-label', 'Model usage and cost');
    }
    tidy(host);
    const next = html(data);
    if (root.__html !== next) {
      hideTip();
      const open = [...root.querySelectorAll('details[open]')].map((x) => x.className);
      root.innerHTML = next;
      root.__html = next;
      open.forEach((c) => { const x = root.querySelector(`details[class="${c}"]`); if (x) x.open = true; });
    }
    root.classList.toggle('is-loading', busy);
  }

  // ── tooltip ─────────────────────────────────────────────────────────────────────────────────────────
  function showTip(col) {
    const chart = col.closest('[data-chart]');
    const it = chart && (charts[chart.dataset.chart] || [])[+col.dataset.i];
    if (!it) return;
    if (!tip) { tip = document.createElement('div'); tip.className = 'hx-ins-tip'; tip.setAttribute('role', 'tooltip'); document.body.appendChild(tip); }
    tip.innerHTML = it.tip;
    tip.style.display = 'block';
    const r = col.getBoundingClientRect(), tr = tip.getBoundingClientRect();
    let x = r.left + r.width / 2 - tr.width / 2;
    x = Math.max(8, Math.min(innerWidth - tr.width - 8, x));
    let y = r.top - tr.height - 8;
    if (y < 8) y = r.bottom + 8;
    tip.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px)`;
  }
  const hideTip = () => { if (tip) tip.style.display = 'none'; };
  document.addEventListener('pointerover', (e) => {
    const c = e.target.closest && e.target.closest('.hx-ins-col');
    if (c) showTip(c); else if (tip && tip.style.display === 'block' && !(e.target.closest && e.target.closest('.hx-ins-tip'))) hideTip();
  });
  document.addEventListener('focusin', (e) => { const c = e.target.closest && e.target.closest('.hx-ins-col'); if (c) showTip(c); else hideTip(); });
  document.addEventListener('scroll', hideTip, true);

  document.addEventListener('click', (e) => {
    const b = e.target.closest && e.target.closest('.hx-ins-win');
    if (b) {
      win = b.dataset.win;
      try { localStorage.setItem('hx-iu-win', win); } catch (_) {}
      hideTip();
      paint();
      return;
    }
    if (e.target.closest && (e.target.closest('.hx-ins-refresh') || e.target.closest('#insightsRefreshBtn'))) {
      if (e.target.closest('.hx-ins-refresh') && typeof window.loadInsights === 'function') window.loadInsights(true);
      setTimeout(() => load(true), 50);
    }
  });

  // Upstream re-renders #insightsContent on open and refresh: strip it back to Tabbie's page + System cards.
  let queued = false;
  const mo = new MutationObserver(() => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      const host = document.getElementById('insightsContent');
      if (!host) return;
      const clean = host.firstElementChild === root && [...host.children].every((el) => el === root || el.classList.contains('hx-ins-sys-h') || KEEP.has(el.id));
      if (clean) return;
      paint();
      if (host.offsetParent) load(false);
    });
  });
  function boot() {
    const host = document.getElementById('insightsContent');
    if (!host) return void setTimeout(boot, 1000);
    mo.observe(host, { childList: true });
    // Opening /insights directly shows the panel without upstream's loadInsights(), which leaves the System
    // cards at "Loading...": ask upstream for them once whenever Insights is shown without them.
    const shown = () => {
      load(false);
      if (!document.getElementById('systemHealthPanel') && typeof window.loadInsights === 'function') window.loadInsights();
    };
    paint();
    if (host.offsetParent) shown();
    new IntersectionObserver((es) => { if (es.some((x) => x.isIntersecting)) shown(); }).observe(host);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, { once: true });
  else boot();
})();
