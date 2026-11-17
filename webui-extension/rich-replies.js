/* Tabbie — chat-app style extras inside replies (plain JS, no build step).
 *  1. Sources: links the worker cites become small source chips (site icon + name) where they sit, and every
 *     reply that cites the web gets a "Sources" row under it that opens a panel of source cards. A trailing
 *     "Sources:" list the worker wrote is folded into that panel instead of showing twice.
 *  2. Galleries: two or more pictures in a row show as one gallery (side by side, or a swipeable strip).
 *  3. Videos: a YouTube link on its own line becomes a video card (thumbnail, title, channel) that plays in place.
 *  4. Follow-ups: a ```tabbie-followups block (one question per line) shows as tappable suggestions under the
 *     newest reply; tapping one sends it.
 * Site icons and video thumbnails come through the sidecar (linkicons.py), never straight from the web: the
 * page's CSP keeps remote images out of the chat on purpose.
 */
(() => {
  'use strict';
  if (window.__hxRich) return;
  window.__hxRich = true;

  const API = 'api/extensions/cc-agents/sidecar/';
  const HOST = '[data-role="assistant"] .msg-body, .assistant-turn .msg-body';
  const SETTLE = 900;
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const icon = (n, s) => (typeof window.hxIcon === 'function' ? window.hxIcon(n, s) : '');
  const host = (u) => { try { return new URL(u).hostname.replace(/^www\./, ''); } catch (_) { return ''; } };
  const SECOND = new Set(['co', 'com', 'org', 'net', 'ac', 'gov', 'edu', 'ltd', 'plc', 'me', 'sch', 'nhs', 'police']);
  const site = (h) => { const l = h.split('.'); return l.slice(-(l.length >= 3 && l[l.length - 1].length === 2 && SECOND.has(l[l.length - 2]) ? 3 : 2)).join('.'); };
  // "en.wikipedia.org" -> "Wikipedia", "bbc.co.uk" -> "BBC"; short names read as acronyms
  const siteName = (h) => { const n = site(h).split('.')[0]; return n.length <= 3 ? n.toUpperCase() : n[0].toUpperCase() + n.slice(1); };
  const favUrl = (h) => `${API}favicon?host=${encodeURIComponent(site(h))}`;
  const ytId = (u) => {
    try {
      const x = new URL(u);
      const h = x.hostname.replace(/^(www|m|music)\./, '');
      if (h === 'youtu.be') return x.pathname.slice(1, 12);
      if (h === 'youtube.com' || h === 'youtube-nocookie.com') {
        if (x.searchParams.get('v')) return x.searchParams.get('v').slice(0, 11);
        const m = x.pathname.match(/^\/(?:shorts|embed|live)\/([\w-]{11})/);
        if (m) return m[1];
      }
    } catch (_) { /* not a url */ }
    return '';
  };
  const validYt = (id) => /^[\w-]{11}$/.test(id || '');
  const faviconImg = (h, cls) => `<span class="${cls}" data-letter="${esc(siteName(h)[0] || '?')}"><img src="${esc(favUrl(h))}" alt="" loading="lazy" decoding="async" onerror="this.remove()"></span>`;
  const external = (a) => /^https?:$/.test(a.protocol) && a.host !== location.host && !a.closest('pre, .hx-src-panel, .hx-yt, .hx-fu');

  // ── 1. sources ───────────────────────────────────────────────────────────
  const SRC_HEAD = /^\s*(?:#+\s*)?(?:sources?|references|citations|further reading)\s*:?\s*$/i;
  function sourceBlocks(body) {
    // a "Sources" heading/paragraph followed by a list of links, or one paragraph "Sources: <links>"
    const out = [];
    for (const el of body.querySelectorAll(':scope > :is(p, h1, h2, h3, h4, h5, h6)')) {
      const txt = el.textContent.trim();
      const next = el.nextElementSibling;
      if (SRC_HEAD.test(txt) && next && /^(UL|OL)$/.test(next.tagName) && next.querySelector('a[href^="http"]')) out.push([el, next]);
      else if (/^sources?\s*:/i.test(txt) && el.tagName === 'P' && el.querySelector('a[href^="http"]')) out.push([el]);
    }
    return out;
  }
  function citeLike(a) {
    const t = a.textContent.trim();
    if (!t) return false;
    if (t === a.href || /^https?:\/\//.test(t) || t.replace(/^www\./, '') === host(a.href)) return true;  // a bare url / domain
    return t.length <= 32 && t.split(/\s+/).length <= 4;                                               // "BBC", "Gov.uk", "MDN docs"
  }
  function decorateSources(body) {
    const seen = new Map(); // url -> {url, host, title}
    const add = (a, title) => {
      const url = a.href.replace(/#.*$/, '');
      const h = host(url);
      if (!h) return;
      const cur = seen.get(url);
      const t = (title || '').trim();
      if (!cur) seen.set(url, { url, host: h, title: t && t !== url && !/^https?:/.test(t) ? t : '' });
      else if (!cur.title && t && t !== url) cur.title = t;
    };
    for (const blk of sourceBlocks(body)) {
      const list = blk[blk.length - 1];
      list.querySelectorAll('a[href^="http"]').forEach((a) => {
        if (!external(a)) return;
        const li = a.closest('li');
        let title = li ? li.textContent.replace(a.textContent, a.textContent).trim() : a.textContent;
        title = title.replace(/^[-–—•\s]+/, '').replace(/\s*[-–—:]\s*$/, '');
        if (title === a.href) title = '';
        add(a, title.length > 140 ? title.slice(0, 139) + '…' : title);
      });
      blk.forEach((el) => el.classList.add('hx-src-folded'));
    }
    for (const a of body.querySelectorAll('a[href^="http"]')) {
      if (!external(a) || a.closest('.hx-src-folded') || a.classList.contains('hx-cite') || ytId(a.href)) continue;
      add(a, a.textContent);
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      if (!citeLike(a)) continue;
      const h = host(a.href);
      const bare = a.textContent.trim() === a.href || /^https?:\/\//.test(a.textContent.trim());
      a.classList.add('hx-cite');
      a.title = a.href;
      a.innerHTML = faviconImg(h, 'hx-cite-ic') + `<span>${esc(bare ? siteName(h) : a.textContent.trim())}</span>`;
    }
    const all = [...seen.values()];
    if (!all.length) return;
    const row = document.createElement('button');
    row.type = 'button';
    row.className = 'hx-src-row';
    row._sources = all;
    const hosts = [...new Set(all.map((s) => site(s.host)))];
    row.innerHTML = `<span class="hx-src-stack">${hosts.slice(0, 4).map((h) => faviconImg(h, 'hx-src-ic')).join('')}</span>
      <span>${all.length} source${all.length === 1 ? '' : 's'}</span>`;
    row.setAttribute('aria-label', `Sources, ${all.length}`);
    const fu = body.querySelector('.hx-fu');
    body.insertBefore(row, fu || null); // sources sit with the answer, follow-ups come last
  }

  // a card's headline: the worker's own words when they say more than the site's name, else the page's path
  function cardTitle(s) {
    const t = (s.title || '').trim();
    if (t && (t.length > 24 || t.split(/\s+/).length > 3) && t.toLowerCase() !== siteName(s.host).toLowerCase()) return t;
    let path = '';
    try { path = decodeURIComponent(new URL(s.url).pathname); } catch (_) { /* keep empty */ }
    const seg = path.split('/').filter(Boolean).reverse().find((x) => /[a-z]{3}/i.test(x.replace(/\.[a-z]+$/i, ''))) || '';
    const words = seg.replace(/\.[a-z0-9]+$/i, '').replace(/[-_+]+/g, ' ').replace(/\s+\d{5,}\b/g, '').trim();
    if (words) return words[0].toUpperCase() + words.slice(1);
    return t || s.host;
  }

  let panel = null;
  function closePanel() {
    if (!panel) return;
    const p = panel;
    panel = null;
    p.classList.remove('is-open');
    setTimeout(() => p.remove(), 220);
    document.removeEventListener('keydown', onPanelKey, true);
  }
  function onPanelKey(e) { if (e.key === 'Escape') { e.stopPropagation(); closePanel(); } }
  function openPanel(sources) {
    closePanel();
    const el = document.createElement('div');
    el.className = 'hx-src-panel';
    el.innerHTML = `<div class="hx-src-scrim"></div><aside class="hx-src-sheet" tabindex="-1" role="dialog" aria-modal="true" aria-label="Sources">
      <header><b>Sources</b><span>${sources.length}</span><button type="button" class="hx-src-x" aria-label="Close">${icon('Cancel01Icon', 16) || '×'}</button></header>
      <div class="hx-src-list">${sources.map((s) => `<a class="hx-src-card" href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">
        <span class="hx-src-site">${faviconImg(s.host, 'hx-src-ic')}<span>${esc(siteName(s.host))}</span><i>${esc(s.host)}</i></span>
        <span class="hx-src-title">${esc(cardTitle(s))}</span></a>`).join('')}</div></aside>`;
    document.body.appendChild(el);
    panel = el;
    requestAnimationFrame(() => el.classList.add('is-open'));
    el.querySelector('.hx-src-scrim').addEventListener('click', closePanel);
    el.querySelector('.hx-src-x').addEventListener('click', closePanel);
    document.addEventListener('keydown', onPanelKey, true);
    el.querySelector('.hx-src-sheet').focus({ preventScroll: true });
  }

  // ── 2. galleries ─────────────────────────────────────────────────────────
  const PIC = 'img.msg-media-img[src*="api/media"]';
  // a picture's unit: upstream's wrapper (picture + download button) when there is one
  const unitOf = (img) => img.closest('.msg-artifact-image') || img;
  const filler = (n) => (n.nodeType === 3 && !n.nodeValue.trim()) || n.nodeName === 'BR';
  // a paragraph that holds nothing but pictures (and line breaks)
  const pure = (p) => p && p.tagName === 'P' && [...p.childNodes].every((n) => filler(n) || (n.nodeType === 1 && (n.matches(PIC) || n.matches('.msg-artifact-image'))));
  function nextUnit(u) {
    let n = u.nextSibling;
    while (n && filler(n)) n = n.nextSibling;
    if (n) return n.nodeType === 1 && (n.matches(PIC) || (n.matches('.msg-artifact-image') && n.querySelector(PIC))) ? n : null;
    // end of a pictures-only paragraph: the next paragraph may carry on the run
    const p = u.parentElement;
    if (!pure(p) || !pure(p.nextElementSibling)) return null;
    const first = [...p.nextElementSibling.childNodes].find((x) => !filler(x));
    return first || null;
  }
  function galleries(body) {
    const units = [...body.querySelectorAll(PIC)].filter((i) => !i.closest('.hx-gal, .hx-chart')).map(unitOf);
    const used = new Set();
    for (const u of units) {
      if (used.has(u)) continue;
      const run = [u];
      let n = nextUnit(u);
      while (n && units.includes(n) && !used.has(n)) { run.push(n); n = nextUnit(n); }
      run.forEach((x) => used.add(x));
      if (run.length < 2) continue;
      const parents = new Set(run.map((x) => x.parentElement));
      const gal = document.createElement('div');
      gal.className = `hx-gal${run.length === 2 ? ' is-two' : ' is-strip'}`;
      gal.setAttribute('role', 'group');
      gal.setAttribute('aria-label', `${run.length} pictures`);
      const anchor = pure(run[0].parentElement) ? run[0].parentElement : run[0];
      anchor.parentNode.insertBefore(gal, anchor);
      run.forEach((x) => gal.appendChild(x));
      parents.forEach((p) => {
        if (p === body || p === gal) return;
        if ([...p.childNodes].every(filler)) p.remove();
      });
      // stray line breaks around the gallery
      for (const sib of [gal.previousSibling, gal.nextSibling]) if (sib && sib.nodeName === 'BR') sib.remove();
    }
  }

  // ── 3. videos ────────────────────────────────────────────────────────────
  const metaCache = new Map();
  async function ytMeta(id) {
    if (!metaCache.has(id)) {
      metaCache.set(id, fetch(`${API}yt/meta?id=${id}`, { credentials: 'same-origin' }).then((r) => (r.ok ? r.json() : {})).catch(() => ({})));
    }
    return metaCache.get(id);
  }
  function videos(body) {
    for (const a of body.querySelectorAll('a[href]')) {
      const id = ytId(a.href);
      if (!validYt(id) || a.closest('.hx-yt, pre, li, td')) continue;
      const p = a.closest('p');
      // only a link standing alone on its line becomes a card; one inside a sentence stays a link
      const line = p ? p.textContent.trim() : '';
      if (!p || (line !== a.textContent.trim() && !(p.querySelectorAll('a').length === 1 && line.replace(a.textContent, '').trim().length <= 1))) continue;
      const card = document.createElement('figure');
      card.className = 'hx-yt';
      card.dataset.id = id;
      card.innerHTML = `<button type="button" class="hx-yt-play" aria-label="Play video">
          <img src="${API}yt/thumb?id=${id}" alt="" loading="lazy" decoding="async" onerror="this.remove()">
          <span class="hx-yt-btn" aria-hidden="true"><svg viewBox="0 0 24 24" width="22" height="22"><path fill="currentColor" d="M8 5.5v13l10.5-6.5z"/></svg></span></button>
        <figcaption><a href="${esc(a.href)}" target="_blank" rel="noopener noreferrer" class="hx-yt-title">YouTube video</a><span class="hx-yt-by">YouTube</span></figcaption>`;
      p.replaceWith(card);
      ytMeta(id).then((m) => {
        if (m && m.title) {
          card.querySelector('.hx-yt-title').textContent = m.title;
          card.querySelector('.hx-yt-play').setAttribute('aria-label', `Play ${m.title}`);
        }
        if (m && m.author) card.querySelector('.hx-yt-by').textContent = `${m.author} · YouTube`;
      });
    }
  }
  function play(card) {
    const id = card.dataset.id;
    if (!validYt(id) || card.querySelector('iframe')) return;
    const f = document.createElement('iframe');
    f.src = `https://www.youtube-nocookie.com/embed/${id}?autoplay=1&playsinline=1&rel=0`;
    f.title = card.querySelector('.hx-yt-title').textContent;
    f.allow = 'autoplay; encrypted-media; picture-in-picture; fullscreen';
    f.allowFullscreen = true;
    f.referrerPolicy = 'strict-origin-when-cross-origin';
    card.querySelector('.hx-yt-play').replaceWith(f);
  }

  // ── 4. follow-ups ────────────────────────────────────────────────────────
  function followups(body) {
    const code = body.querySelector('pre > code.language-tabbie-followups');
    if (!code) return;
    const pre = code.parentElement;
    const head = pre.previousElementSibling && pre.previousElementSibling.classList.contains('pre-header') ? pre.previousElementSibling : null;
    [pre, head, pre.parentElement && pre.parentElement.classList.contains('pre-wrap') ? pre.parentElement : null].forEach((e) => e && e.classList.add('hx-fu-src'));
    const qs = code.textContent.split('\n').map((l) => l.replace(/^\s*(?:[-*•]|\d+[.)])\s*/, '').trim()).filter((l) => l && l.length <= 160).slice(0, 4);
    if (!qs.length || body.querySelector('.hx-fu')) return;
    const box = document.createElement('div');
    box.className = 'hx-fu';
    box.setAttribute('aria-label', 'Suggested follow-ups');
    box.innerHTML = qs.map((q) => `<button type="button" class="hx-fu-q"><span>${esc(q)}</span>${icon('ArrowRight01Icon', 13)}</button>`).join('');
    body.appendChild(box);
    const src = body.querySelector('.hx-src-row');
    if (src) body.insertBefore(src, box);
  }
  // only the newest reply offers follow-ups (older ones were already answered or moved past)
  function newestOnly() {
    const all = [...document.querySelectorAll('#messages .hx-fu')];
    const rows = [...document.querySelectorAll('#messages .msg-row')];
    const last = rows[rows.length - 1];
    all.forEach((f) => f.classList.toggle('is-stale', !(last && last.contains(f))));
  }
  function sendText(text) {
    const t = document.getElementById('msg');
    if (!t || typeof window.send !== 'function' && typeof send !== 'function') return;
    t.value = text;
    t.dispatchEvent(new Event('input', { bubbles: true }));
    try { (window.send || send)(); } catch (e) { console.warn('rich-replies send', e); }
  }

  // ── wiring ───────────────────────────────────────────────────────────────
  document.addEventListener('click', (e) => {
    const row = e.target.closest('.hx-src-row');
    if (row) { e.preventDefault(); openPanel(row._sources || []); return; }
    const yt = e.target.closest('.hx-yt-play');
    if (yt) { e.preventDefault(); play(yt.closest('.hx-yt')); return; }
    const fu = e.target.closest('.hx-fu-q');
    if (fu && !fu.closest('.is-stale')) { e.preventDefault(); fu.closest('.hx-fu').classList.add('is-sent'); sendText(fu.textContent.trim()); }
  });

  // tables: a rounded, scrollable card (CSS does the look)
  function tables(body) {
    body.querySelectorAll('table').forEach((t) => {
      if (t.closest('.hx-table, .hx-chart, pre, .csv-table-wrap')) return;
      const w = document.createElement('div');
      w.className = 'hx-table';
      t.parentNode.insertBefore(w, t);
      w.appendChild(t);
    });
  }

  function decorate(body) {
    try { tables(body); } catch (e) { console.warn('rich tables', e); }
    try { followups(body); } catch (e) { console.warn('rich followups', e); }
    try { videos(body); } catch (e) { console.warn('rich videos', e); }
    try { galleries(body); } catch (e) { console.warn('rich galleries', e); }
    try { decorateSources(body); } catch (e) { console.warn('rich sources', e); }
  }

  // decorated bodies carry a hidden marker; upstream re-rendering a message drops it, and the body is done again
  const MARK = 'hx-rich-mark';
  const seen = new WeakMap();
  let timer = 0;
  const liveTurn = (body) => !!body.closest('.msg-row')?.querySelector('[data-live-assistant="1"]');
  const streaming = () => !!document.querySelector('#hxLiveStatus, [data-live-assistant="1"]');
  function sweep() {
    timer = 0;
    let again = false;
    document.querySelectorAll(HOST).forEach((body) => {
      if (body.querySelector(`:scope > .${MARK}`)) return;
      if (liveTurn(body)) { again = true; return; }
      // finished messages (a reload, a reply that just landed) are done at once, so raw markup never shows;
      // only while a reply is still being written do we wait for its text to hold still
      if (streaming()) {
        const text = body.textContent;
        if (seen.get(body) !== text) { seen.set(body, text); again = true; return; }
      }
      seen.delete(body);
      decorate(body);
      const m = document.createElement('i');
      m.className = MARK;
      m.hidden = true;
      body.appendChild(m);
    });
    newestOnly();
    if (again) schedule();
  }
  function schedule() { if (!timer) timer = setTimeout(sweep, streaming() ? SETTLE : 30); }
  new MutationObserver((list) => {
    for (const m of list) {
      const el = m.target.nodeType === 1 ? m.target : m.target.parentElement;
      if (el && el.closest && el.closest('.hx-src-row, .hx-src-panel, .hx-yt, .hx-fu, .hx-gal, .hx-cite, .hx-rich-mark')) continue;
      schedule();
      return;
    }
  }).observe(document.body, { childList: true, subtree: true, characterData: true });
  schedule();

  window.hxRich = { sweep, decorate };
})();
