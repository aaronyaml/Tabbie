/* Tabbie — worker tokens on Insights. Upstream Insights counts the hub's own tokens; the Claude Code and
 * Codex workers that do the jobs run outside it. This card adds them up per model for the chosen period
 * (sidecar GET /tokens, from each request's recorded usage).
 */
(() => {
  'use strict';
  if (window.__hxWorkerTokens) return;
  window.__hxWorkerTokens = true;

  const API = 'api/extensions/cc-agents/sidecar/tokens?days=';
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmt = (n) => (n >= 1e6 ? (n / 1e6).toFixed(n >= 1e7 ? 0 : 1) + 'M' : n >= 1e3 ? (n / 1e3).toFixed(n >= 1e4 ? 0 : 1) + 'k' : String(n));
  const pct = (a, b) => (b ? Math.round((100 * a) / b) + '%' : '–');
  const NAMES = { 'claude-opus-5-5': 'Opus 5.5', 'claude-sonnet-5-5': 'Sonnet 5.5', 'claude-haiku-5-5': 'Haiku 5.5',
    'claude-fable-5-1': 'Fable 5.1', 'gpt-6.1-sol': 'GPT-6.1 Sol', 'gpt-6.1-terra': 'GPT-6.1 Terra',
    'gpt-6-astra': 'GPT-6 Astra', 'gpt-6-luna': 'GPT-6 Luna' };

  function days() {
    const v = (document.getElementById('insightsPeriod') || {}).value;
    return Math.min(90, parseInt(v, 10) || 30);
  }

  let last = null;  // {days, data}
  let busy = false;
  async function load() {
    const n = days();
    if (busy || (last && last.days === n && Date.now() - last.at < 30000)) return paint();
    busy = true;
    try {
      const r = await fetch(API + n, { credentials: 'same-origin' });
      if (!r.ok) throw new Error(String(r.status));
      last = { days: n, at: Date.now(), data: await r.json() };
    } catch (_) {
      last = null;
    } finally { busy = false; }
    paint();
  }

  function html(d) {
    const t = d.total;
    const rows = Object.entries(d.by_model).map(([m, v]) => `<div class="insights-table-row hx-wt-row">
        <span class="insights-model-name">${esc(NAMES[m] || m)}</span><span>${v.runs}</span>
        <span>${fmt(v.input)}</span><span>${pct(v.cached, v.input)}</span><span>${fmt(v.output)}</span></div>`).join('');
    const older = t.unknown ? `<p class="hx-wt-note">${t.unknown} older run${t.unknown === 1 ? '' : 's'} from before Tabbie recorded worker tokens ${t.unknown === 1 ? "isn't" : "aren't"} counted.</p>` : '';
    if (!t.runs) return `<div class="insights-card-title">Workers</div>
      <p class="hx-wt-note">Claude Code and Codex token use shows here from the next job on.</p>${older}`;
    return `<div class="insights-card-title">Workers</div>
      <p class="hx-wt-sub">Claude Code and Codex jobs, last ${d.days} day${d.days === 1 ? '' : 's'} (the hub's own tokens are below)</p>
      <div class="hx-wt-stats">
        <div class="hx-wt-stat"><span>Runs</span><b>${t.runs}</b></div>
        <div class="hx-wt-stat"><span>Tokens in</span><b>${fmt(t.input)}</b><i>${pct(t.cached, t.input)} cached</i></div>
        <div class="hx-wt-stat"><span>Tokens out</span><b>${fmt(t.output)}</b></div>
      </div>
      ${rows ? `<div class="insights-table hx-wt-table"><div class="insights-table-head hx-wt-row"><span>Model</span><span>Runs</span><span>In</span><span>Cached</span><span>Out</span></div>${rows}</div>` : ''}
      ${older}`;
  }

  function paint() {
    const host = document.getElementById('insightsContent');
    if (!host || !last) return;
    let card = host.querySelector('.hx-wt');
    if (!card) {
      card = document.createElement('div');
      card.className = 'insights-card hx-wt';
      const health = host.querySelector('.system-health-panel');
      if (health && health.nextSibling) health.parentNode.insertBefore(card, health.nextSibling);
      else host.prepend(card);
    }
    const next = html(last.data);
    if (card.__html !== next) { card.innerHTML = next; card.__html = next; }
  }

  // Upstream re-renders #insightsContent on open, refresh and period change: put the card back each time.
  let queued = false;
  const mo = new MutationObserver(() => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      const host = document.getElementById('insightsContent');
      if (host && host.offsetParent && !host.querySelector('.hx-wt') && host.querySelector('.insights-card')) load();
    });
  });
  function boot() {
    const host = document.getElementById('insightsContent');
    if (!host) return void setTimeout(boot, 1000);
    mo.observe(host, { childList: true });
    document.addEventListener('change', (e) => { if (e.target && e.target.id === 'insightsPeriod') { last = null; setTimeout(load, 300); } });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, { once: true });
  else boot();
})();
