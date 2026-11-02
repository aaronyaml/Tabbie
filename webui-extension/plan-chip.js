/* Tabbie — the planner's brief, behind a small chip in the reply. After you approve a plan, the
 * planner (GPT) writes the final brief Claude executes. The hub puts `[[plan:<request id>]]` on its
 * own line in the reply (command-centre directive); this turns it into a collapsed "Plan" chip that
 * loads the brief from the helper (GET …/sidecar/plan?id=) only when opened.
 */
(() => {
  'use strict';
  if (window.__hxPlanChip) return;
  window.__hxPlanChip = true;

  const API = 'api/extensions/cc-agents/sidecar/plan?id=';
  const TOKEN = /\[\[plan:(\d{8}-\d{6}-[0-9a-f]{6})\]\]/;
  const cache = new Map();
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const icon = (n, s) => (typeof window.hxIcon === 'function' ? window.hxIcon(n, s) : '');

  function chip(rid) {
    const d = document.createElement('details');
    d.className = 'hx-plan';
    d.dataset.rid = rid;
    d.innerHTML = `<summary>${icon('CheckListIcon', 14)}<span>Plan</span><em>tap to read</em></summary><div class="hx-plan-body"></div>`;
    d.addEventListener('toggle', () => { if (d.open) load(d); });
    d.addEventListener('click', (e) => {
      const b = e.target.closest('[data-plan-retry], [data-plan-allow]');
      if (!b) return;
      e.preventDefault();
      if (b.hasAttribute('data-plan-allow') && window.hxHelperConsent) window.hxHelperConsent.open();
      else load(d);
    });
    return d;
  }

  async function load(d) {
    const body = d.querySelector('.hx-plan-body');
    if (body.dataset.done) return;
    body.innerHTML = '<p class="hx-plan-dim">Loading…</p>';
    try {
      let p = cache.get(d.dataset.rid);
      if (!p) {
        const r = await fetch(API + encodeURIComponent(d.dataset.rid), { credentials: 'same-origin', cache: 'no-store' });
        if (r.status === 401 || r.status === 403) {
          if (window.hxHelperConsent) window.hxHelperConsent.open();
          body.innerHTML = `<p class="hx-plan-dim">Allow Tabbie's helper on this computer to read the plan.</p>${window.hxHelperConsent ? '<button type="button" class="hx-plan-retry" data-plan-allow>Allow</button>' : ''}`;
          return;
        }
        if (!r.ok) throw new Error(String(r.status));
        p = await r.json();
        cache.set(d.dataset.rid, p);
      }
      if (!p.brief) { body.innerHTML = '<p class="hx-plan-dim">No brief was saved for this request.</p>'; return; }
      const who = p.truncated ? '<p class="hx-plan-dim">Shortened here; the full brief is in the request folder.</p>' : '';
      const md = typeof window.renderMd === 'function' ? window.renderMd(p.brief) : `<pre>${esc(p.brief)}</pre>`;
      body.innerHTML = who + `<div class="hx-plan-md">${md}</div>`;
      body.dataset.done = '1';
      const em = d.querySelector('summary em');
      if (em) em.textContent = p.planner ? p.planner.split(':').pop() : '';
    } catch (e) {
      const down = e instanceof TypeError || /^5\d\d$/.test(String(e && e.message));
      body.innerHTML = `<p class="hx-plan-dim">${down ? "Can't reach Tabbie on your computer right now." : `Couldn't load the plan (${esc((e && e.message) || e)}).`}</p><button type="button" class="hx-plan-retry" data-plan-retry>Try again</button>`;
    }
  }

  function scan() {
    document.querySelectorAll('.msg-body, .assistant-segment').forEach((root) => {
      if (!TOKEN.test(root.textContent || '')) return;
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      const hits = [];
      for (let n = walker.nextNode(); n; n = walker.nextNode()) {
        if (TOKEN.test(n.nodeValue) && !(n.parentElement && n.parentElement.closest('code, pre, .hx-plan'))) hits.push(n);
      }
      for (const n of hits) {
        const m = TOKEN.exec(n.nodeValue);
        const para = n.parentElement;
        const alone = para && para.tagName === 'P' && para.textContent.trim() === m[0];
        if (alone) { para.replaceWith(chip(m[1])); continue; }
        const after = n.splitText(m.index);
        after.nodeValue = after.nodeValue.slice(m[0].length);
        after.parentNode.insertBefore(chip(m[1]), after);
      }
    });
  }

  if (window.hxDom) window.hxDom.on(scan);  // shared, throttled page watcher (hx-dom.js)
  else {
    let queued = false;
    new MutationObserver(() => {
      if (queued) return;
      queued = true;
      requestAnimationFrame(() => { queued = false; scan(); });
    }).observe(document.body, { childList: true, subtree: true, characterData: true });
  }
  scan();
})();
