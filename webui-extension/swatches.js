/* Tabbie — colour swatches in replies (plain JS, no build step).
 * A reply that names hex colours (#FDBB1B, #fff, #RRGGBBAA) shows each one right where it's written: a colour chip
 * in front of the code; tap it to see the colour big, with its RGB, and copy either.
 * Works on any assistant message, live or reloaded; code blocks are left alone. Nothing to configure.
 */
(() => {
  'use strict';
  if (window.__hxSwatches) return;
  window.__hxSwatches = true;

  // #abc only counts with a letter in it (#123 is usually an issue number); 4-digit forms are skipped.
  const RX = /(^|[^\w&#/=])#([0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3})(?![\w-])/g;
  const SKIP = 'pre, a, textarea, input, script, style, svg, .hx-sw, .hx-chart, .katex, .mermaid';
  const HOST = '[data-role="assistant"] .msg-body, .assistant-turn .msg-body, .assistant-segment .msg-body';
  const SETTLE = 900; // ms a message must stay unchanged before it's decorated (never mid-stream)

  const norm = (h) => {
    h = h.toUpperCase();
    if (h.length === 3) h = h.split('').map((c) => c + c).join('');
    return '#' + h;
  };
  const valid = (h) => h.length !== 3 || /[a-f]/i.test(h);
  const rgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  const alpha = (hex) => (hex.length === 9 ? Math.round(parseInt(hex.slice(7, 9), 16) / 2.55) : 100);
  const lum = (hex) => {
    const c = rgb(hex).map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; });
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
  };

  function chip(hex) {
    const s = document.createElement('span');
    s.className = 'hx-sw';
    s.style.setProperty('--sw', hex);
    s.dataset.hex = hex;
    s.setAttribute('role', 'button');
    s.setAttribute('tabindex', '0');
    s.setAttribute('aria-label', `Show colour ${hex}`);
    return s;
  }

  // wrap hex codes in one text node; returns the colours found
  function decorateText(node, found) {
    const t = node.nodeValue;
    RX.lastIndex = 0;
    if (!RX.test(t)) return;
    RX.lastIndex = 0;
    const frag = document.createDocumentFragment();
    let last = 0, m, hit = false;
    while ((m = RX.exec(t))) {
      if (!valid(m[2])) continue;
      const start = m.index + m[1].length;
      const hex = norm(m[2]);
      frag.appendChild(document.createTextNode(t.slice(last, start)));
      // inside inline code or bold, the chip goes in front of the whole element instead
      frag.appendChild(chip(hex));
      frag.appendChild(document.createTextNode(t.slice(start, start + 1 + m[2].length)));
      last = start + 1 + m[2].length;
      found.push(hex);
      hit = true;
    }
    if (!hit) return;
    frag.appendChild(document.createTextNode(t.slice(last)));
    node.parentNode.replaceChild(frag, node);
  }

  function scan(body) {
    const found = [];
    const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT, {
      acceptNode: (n) => (n.nodeValue.includes('#') && !n.parentElement.closest(SKIP)
        ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT),
    });
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach((n) => decorateText(n, found));
    // chips that landed inside <code>: move them out so the code pill stays clean
    body.querySelectorAll('code > .hx-sw').forEach((s) => {
      const code = s.parentElement;
      if (!code.closest('pre')) code.parentNode.insertBefore(s, code);
    });
    return found;
  }

  const done = new WeakMap(); // body -> its text once decorated (re-rendered bodies come back with new text)
  function decorate(body) {
    const n = scan(body).length;
    done.set(body, { text: body.textContent, n });
  }

  // ── big view ──────────────────────────────────────────────────────────────
  let pop = null;
  function close() {
    if (!pop) return;
    pop.remove();
    pop = null;
    document.removeEventListener('keydown', onKey, true);
  }
  function onKey(e) { if (e.key === 'Escape') { e.stopPropagation(); close(); } }

  function open(hex, anchor) {
    close();
    const [r, g, b] = rgb(hex);
    const a = alpha(hex);
    pop = document.createElement('div');
    pop.className = 'hx-sw-pop';
    pop.setAttribute('role', 'dialog');
    pop.setAttribute('aria-label', `Colour ${hex}`);
    pop.innerHTML = `<div class="hx-sw-big${lum(hex) > 0.82 ? ' is-light' : ''}" style="--sw:${hex}"></div>
      <div class="hx-sw-info">
        <div class="hx-sw-row"><b>${hex}</b><button type="button" class="hx-sw-copy" data-copy="${hex}">Copy</button></div>
        <div class="hx-sw-row"><span>RGB ${r}, ${g}, ${b}${a < 100 ? ` · ${a}%` : ''}</span><button type="button" class="hx-sw-copy" data-copy="rgb(${r}, ${g}, ${b})">Copy</button></div>
      </div>`;
    document.body.appendChild(pop);
    const box = anchor.getBoundingClientRect();
    const w = pop.offsetWidth, h = pop.offsetHeight, pad = 12;
    let x = Math.min(Math.max(pad, box.left + box.width / 2 - w / 2), innerWidth - w - pad);
    let y = box.top - h - 8;
    if (y < pad + (parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--app-titlebar-safe-top')) || 0)) y = box.bottom + 8;
    pop.style.left = `${Math.round(x)}px`;
    pop.style.top = `${Math.round(Math.min(y, innerHeight - h - pad))}px`;
    document.addEventListener('keydown', onKey, true);
  }

  async function copy(btn) {
    const text = btn.dataset.copy;
    try { await navigator.clipboard.writeText(text); } catch (_) {
      const t = document.createElement('textarea');
      t.value = text; t.style.position = 'fixed'; t.style.opacity = '0';
      document.body.appendChild(t); t.select();
      try { document.execCommand('copy'); } catch (__) { /* nothing more to try */ }
      t.remove();
    }
    btn.textContent = 'Copied';
    btn.classList.add('is-done');
    setTimeout(() => { btn.textContent = 'Copy'; btn.classList.remove('is-done'); }, 1400);
  }

  document.addEventListener('click', (e) => {
    const c = e.target.closest('.hx-sw-copy');
    if (c) { e.preventDefault(); copy(c); return; }
    const t = e.target.closest('.hx-sw');
    if (t) {
      e.preventDefault();
      e.stopPropagation();
      if (pop && pop.dataset.for === t.dataset.hex && pop._anchor === t) { close(); return; }
      open(t.dataset.hex, t);
      pop.dataset.for = t.dataset.hex;
      pop._anchor = t;
      return;
    }
    if (pop && !e.target.closest('.hx-sw-pop')) close();
  }, true);
  document.addEventListener('keydown', (e) => {
    if ((e.key === 'Enter' || e.key === ' ') && e.target.classList && e.target.classList.contains('hx-sw')) {
      e.preventDefault();
      e.target.click();
    }
  });
  addEventListener('resize', close);
  document.addEventListener('scroll', close, true);

  // ── watch messages; decorate each once it has stopped changing ─────────────
  const seen = new WeakMap(); // body -> text it had when last looked at
  let timer = 0;
  function sweep() {
    timer = 0;
    let again = false;
    document.querySelectorAll(HOST).forEach((body) => {
      const text = body.textContent;
      const d = done.get(body);
      if (!text.includes('#') || (d && d.text === text && (!d.n || body.querySelector('.hx-sw')))) { seen.delete(body); return; }
      if (seen.get(body) === text) { seen.delete(body); decorate(body); return; }
      seen.set(body, text);
      again = true;
    });
    if (again) schedule();
  }
  function schedule() { if (!timer) timer = setTimeout(sweep, SETTLE); }
  new MutationObserver((list) => {
    for (const m of list) {
      const el = m.target.nodeType === 1 ? m.target : m.target.parentElement;
      if (el && el.closest && el.closest('.hx-sw, .hx-sw-pop')) continue;
      schedule();
      return;
    }
  }).observe(document.body, { childList: true, subtree: true, characterData: true });
  schedule();

  window.hxSwatches = { sweep, scan };
})();
