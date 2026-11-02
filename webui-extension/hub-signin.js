/* Tabbie — "Finish setting up Tabbie" card: signs the hub in when it has no AI login yet.
 * Shown on load when the WebUI reports chat isn't ready, or as soon as a chat shows
 * "No LLM provider configured". Subscription logins only, never API keys:
 *   ChatGPT  WebUI's device-code sign-in (POST api/onboarding/oauth/start {provider: openai-codex}):
 *            open the link on any device, type the code. Works from a phone too.
 *   Claude   WebUI links Claude Code's login on the Mac ({provider: anthropic}); if Claude Code isn't
 *            signed in yet, the sidecar starts `claude auth login` on the Mac.
 * Then the sidecar points the hub at that login (POST …/sidecar/hub {provider}, config backed up).
 */
(() => {
  'use strict';
  if (window.__hxHubSignin) return;
  window.__hxHubSignin = true;

  const SIDE = 'api/extensions/cc-agents/sidecar/';
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const NAME = { 'openai-codex': 'ChatGPT', anthropic: 'Claude' };

  async function get(url) {
    const r = await fetch(url, { credentials: 'same-origin', cache: 'no-store' });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || String(r.status));
    return j;
  }
  async function post(url, body) {
    const r = await fetch(url, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || String(r.status));
    return j;
  }

  let root = null, flow = null, timer = null, shown = false;
  function mount() {
    if (root) return;
    root = document.createElement('div');
    root.id = 'hxHubSignin';
    root.innerHTML = '<div class="hx-hs-scrim"></div><div class="hx-hs-card" role="dialog" aria-modal="true" aria-labelledby="hxHsTitle"></div>';
    document.body.appendChild(root);
    root.addEventListener('click', onClick);
  }
  function paint(html) { mount(); root.querySelector('.hx-hs-card').innerHTML = html; root.classList.add('is-open'); shown = true; }
  function close() { stop(); if (root) root.classList.remove('is-open'); shown = false; }
  function stop() { if (timer) clearTimeout(timer); timer = null; }

  async function choose() {
    let on = ['codex', 'claude'];
    try { on = (await get(SIDE + 'agents')).enabled || on; } catch (_) {}
    const opts = [];
    if (on.includes('codex')) opts.push(['openai-codex', 'Sign in with ChatGPT', 'Uses your ChatGPT subscription. You can do this from any device.']);
    if (on.includes('claude')) opts.push(['anthropic', 'Use my Claude login', 'Uses the Claude account Claude Code is signed in with on your Mac.']);
    paint(`<h2 id="hxHsTitle">Finish setting up Tabbie</h2>
      <p>Tabbie needs to sign in to your AI account before it can chat. No API key needed.</p>
      <div class="hx-hs-opts">${opts.map(([p, t, d], i) => `<button type="button" class="hx-hs-opt${i ? '' : ' is-primary'}" data-p="${p}"><b>${t}</b><span>${d}</span></button>`).join('')}</div>
      <div class="hx-hs-foot"><button type="button" class="hx-hs-btn" data-close>Later</button></div>`);
  }

  async function start(provider) {
    stop();
    paint(`<h2 id="hxHsTitle">Signing in with ${NAME[provider]}…</h2><p class="hx-hs-dim">One moment.</p>`);
    try { flow = await post('api/onboarding/oauth/start', { provider }); }
    catch (e) { return fail(e.message, provider); }
    flow.provider = provider;
    step();
  }

  function step() {
    const f = flow;
    if (f.status === 'success') return finish(f.provider);
    if (f.status && f.status !== 'pending') return fail(f.error || `Sign-in ${f.status}.`, f.provider);
    if (f.provider === 'openai-codex' && f.user_code) {
      if (!/^https:\/\//i.test(String(f.verification_uri || ''))) return fail('Sign-in returned an unexpected link.', f.provider);
      paint(`<h2 id="hxHsTitle">Sign in with ChatGPT</h2>
        <ol class="hx-hs-steps"><li>Open <a href="${esc(f.verification_uri)}" target="_blank" rel="noopener noreferrer">${esc(f.verification_uri.replace(/^https?:\/\//, ''))}</a> (on this device or any other)</li>
        <li>Sign in to ChatGPT and enter this code:</li></ol>
        <div class="hx-hs-code"><span>${esc(f.user_code)}</span><button type="button" class="hx-hs-btn" data-copy="${esc(f.user_code)}">Copy</button></div>
        <p class="hx-hs-dim"><span class="hx-hs-spin"></span>Waiting for you to finish…</p>
        <div class="hx-hs-foot"><button type="button" class="hx-hs-btn" data-cancel>Cancel</button></div>`);
    } else if (f.provider === 'anthropic') {
      paint(`<h2 id="hxHsTitle">Use your Claude login</h2>
        <p>Claude Code isn't signed in on your Mac yet. Sign it in, and Tabbie will pick it up automatically.</p>
        <button type="button" class="hx-hs-btn is-primary" data-claude>Open Claude sign-in on the Mac</button>
        <p class="hx-hs-dim">This opens claude.ai in the Mac's browser. Not at the Mac? Run <code>claude auth login</code> there later.</p>
        <p class="hx-hs-dim"><span class="hx-hs-spin"></span>Waiting for Claude Code's login…</p>
        <div class="hx-hs-foot"><button type="button" class="hx-hs-btn" data-cancel>Cancel</button></div>`);
    }
    timer = setTimeout(poll, Math.max(2, +f.poll_interval_seconds || 4) * 1000);
  }
  async function poll() {
    try { Object.assign(flow, await get('api/onboarding/oauth/poll?flow_id=' + encodeURIComponent(flow.flow_id))); }
    catch (_) { /* transient: keep waiting */ }
    if (shown) step();
  }

  async function finish(provider) {
    stop();
    try { await post(SIDE + 'hub', { provider }); }
    catch (e) { return fail(`Signed in, but couldn't save it (${e.message}). Run bin/setup on the Mac.`, provider); }
    paint(`<h2 id="hxHsTitle">You're all set</h2>
      <p>Tabbie is signed in with ${NAME[provider]}. Send your message again.</p>
      <div class="hx-hs-foot"><button type="button" class="hx-hs-btn is-primary" data-close>Done</button></div>`);
  }
  function fail(msg, provider) {
    stop();
    paint(`<h2 id="hxHsTitle">Couldn't sign in</h2><p class="hx-hs-err">${esc(msg)}</p>
      <div class="hx-hs-foot"><button type="button" class="hx-hs-btn" data-back>Back</button>
      <button type="button" class="hx-hs-btn is-primary" data-p="${esc(provider)}">Try again</button></div>`);
  }

  async function onClick(e) {
    if (e.target.classList.contains('hx-hs-scrim')) return close();
    const t = e.target.closest('button');
    if (!t) return;
    if (t.dataset.p) return start(t.dataset.p);
    if (t.hasAttribute('data-close')) return close();
    if (t.hasAttribute('data-back')) return choose();
    if (t.dataset.copy) { try { await navigator.clipboard.writeText(t.dataset.copy); t.textContent = 'Copied'; } catch (_) {} return; }
    if (t.hasAttribute('data-cancel')) {
      try { if (flow) await post('api/onboarding/oauth/cancel', { flow_id: flow.flow_id, provider: flow.provider }); } catch (_) {}
      return choose();
    }
    if (t.hasAttribute('data-claude')) {
      try { const j = await post(SIDE + 'hub/claude-login', {}); if (j.ok === false) throw new Error(j.error); t.textContent = 'Opened on the Mac'; t.disabled = true; }
      catch (err) { fail(err.message, 'anthropic'); }
    }
  }

  async function check() {
    if (shown) return;
    try {
      const s = await get('api/onboarding/status');
      if (s && s.system && s.system.hermes_found && s.system.imports_ok && s.system.chat_ready === false) choose();
    } catch (_) { /* logged out or older WebUI: stay quiet */ }
  }
  // A chat that already failed for this reason opens the card straight away.
  new MutationObserver(() => {
    if (shown) return;
    const hit = [...document.querySelectorAll('.msg-error, .error, [class*="error"]')].some((el) => /No LLM provider configured/i.test(el.textContent || ''));
    if (hit) choose();
  }).observe(document.body, { childList: true, subtree: true });
  window.hxHubSignin = { open: choose };
  setTimeout(check, 1500);
})();
