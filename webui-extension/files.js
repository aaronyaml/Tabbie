/* Hermes — file cards + in-site viewer (plain JS, no build step). Replaces media-fix.js.
 *  1. User uploads ("[Attached files: …]" marker / upstream .msg-files) -> a row of file cards.
 *  2. Assistant replies: `MEDIA:/p` and `MEDIA: /p` -> video/audio stay inline players (rounded,
 *     with a name/download row); other files -> cards. Upstream's 📎 links, file badges and
 *     PDF previews -> cards. Absolute paths / file links that the server will serve (probed,
 *     so the allowed roots stay the server's call) -> cards under the reply.
 *  3. Card: Hugeicon or thumbnail, middle-truncated name, "TYPE · size", download button.
 *     Viewable types open a modal viewer (sheet on phones); archives/binaries only download.
 * Serving: /api/media (byte ranges, allow-listed roots); PDFs render with PDF.js, page per screen.
 */
(() => {
  'use strict';
  if (window.__hxFiles) return;
  window.__hxFiles = true;

  // ── kinds ────────────────────────────────────────────────────────────────
  const KIND = [
    ['image', /^(png|jpe?g|gif|webp|bmp|avif|ico|svg)$/],
    ['video', /^(mp4|m4v|mov|webm|ogv)$/],
    ['audio', /^(mp3|m4a|wav|aac|ogg|oga|opus|flac)$/],
    ['pdf', /^pdf$/],
    ['text', /^(md|markdown|txt|text|log|json|jsonl|ndjson|csv|tsv|ya?ml|toml|ini|cfg|conf|xml|html?|css|scss|m?js|cjs|jsx|tsx?|py|rb|go|rs|sh|bash|zsh|swift|kt|java|c|h|cc|cpp|hpp|sql|diff|patch|env|gitignore|lock)$/],
  ];
  const ICON = { image: 'FileImageIcon', video: 'FileVideoIcon', audio: 'FileAudioIcon', pdf: 'Pdf02Icon',
    archive: 'FileZipIcon', code: 'FileCodeIcon', doc: 'FileTextIcon', sheet: 'FileSpreadsheetIcon', file: 'File01Icon' };
  const ARCHIVE = /^(zip|7z|rar|tar|gz|tgz|bz2|xz|zst|dmg|iso|pkg|exe|msi|deb|rpm|apk|ipa|jar|app)$/;
  const CODE = /^(json|jsonl|ndjson|ya?ml|toml|ini|cfg|conf|xml|html?|css|scss|m?js|cjs|jsx|tsx?|py|rb|go|rs|sh|bash|zsh|swift|kt|java|c|h|cc|cpp|hpp|sql|diff|patch|env)$/;
  const TEXT_CAP = 1024 * 1024;

  const extOf = (n) => { const m = /\.([A-Za-z0-9]{1,10})$/.exec(n || ''); return m ? m[1].toLowerCase() : ''; };
  const baseName = (p) => String(p || '').split('/').pop() || String(p || '');
  function kindOf(name) {
    const e = extOf(name);
    for (const [k, rx] of KIND) if (rx.test(e)) return k;
    return ARCHIVE.test(e) ? 'archive' : 'file';
  }
  function iconOf(name) {
    const k = kindOf(name), e = extOf(name);
    if (k === 'text') return /^(csv|tsv)$/.test(e) ? ICON.sheet : CODE.test(e) ? ICON.code : ICON.doc;
    return ICON[k] || ICON.file;
  }
  const icon = (name, size) => (typeof window.hxIcon === 'function' ? window.hxIcon(name, size) : '');
  function fmtSize(n) {
    if (n == null || isNaN(n)) return '';
    if (n < 1000) return n + ' B';
    const u = ['KB', 'MB', 'GB', 'TB'];
    let i = -1;
    do { n /= 1000; i++; } while (n >= 1000 && i < u.length - 1);
    return n.toFixed(n < 10 || i > 0 ? 1 : 0) + ' ' + u[i];
  }
  const typeLabel = (name) => (extOf(name) || 'file').toUpperCase();

  // ── sources: one file -> view / download URLs ───────────────────────────
  function sidOf(el) {
    const row = el && el.closest && el.closest('[data-session-id]');
    if (row) return row.getAttribute('data-session-id');
    try { return (S.session && S.session.session_id) || ''; } catch (_) { return ''; }
  }
  const enc = encodeURIComponent;
  // path: absolute file path; else name only (an in-flight upload: file/raw resolves it).
  function source(path, sid, opts) {
    const name = baseName(path);
    const s = { name, path, sid, kind: kindOf(name), attach: !!(opts && opts.attach) };
    if (path.startsWith('/')) {
      s.url = 'api/media?path=' + enc(path) + (sid ? '&session_id=' + enc(sid) : '');
      s.dl = s.url;
    } else {
      s.url = 'api/file/raw?session_id=' + enc(sid) + '&path=' + enc(path);
      s.dl = s.url + '&download=1';
    }
    s.play = s.url + (s.url.includes('?') ? '&' : '?') + 'inline=1';
    return s;
  }

  // Size (and "is it servable") via a 1-byte range request; cached per URL.
  const probes = new Map();
  function probe(url) {
    if (!probes.has(url)) {
      if (probes.size > 400) probes.clear();
      probes.set(url, fetch(url, { headers: { Range: 'bytes=0-0' }, credentials: 'same-origin' }).then((r) => {
        const cr = r.headers.get('Content-Range') || '';
        const total = /\/(\d+)$/.exec(cr);
        const ok = r.status === 200 || r.status === 206 || r.status === 416;
        const size = total ? +total[1] : r.status === 200 ? +(r.headers.get('Content-Length') || NaN) : null;
        try { r.body && r.body.cancel(); } catch (_) {}
        return { ok, size };
      }).catch(() => ({ ok: false, size: null })));
    }
    return probes.get(url);
  }

  // ── card ─────────────────────────────────────────────────────────────────
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const viewable = (k) => k === 'image' || k === 'video' || k === 'audio' || k === 'pdf' || k === 'text';
  function nameHtml(name) {
    // Middle truncation: the head ellipsizes, the tail (last ~9 chars incl. extension) stays.
    const cut = Math.max(0, name.length - 9);
    return `<span class="hx-nm-a">${esc(name.slice(0, cut))}</span><span class="hx-nm-b">${esc(name.slice(cut))}</span>`;
  }
  function card(s) {
    const el = document.createElement('div');
    el.className = 'hx-file';
    el.setAttribute('data-kind', s.kind);
    el.setAttribute('data-hx-path', s.path);
    el.setAttribute('role', 'button');
    el.setAttribute('tabindex', '0');
    el.setAttribute('aria-label', (viewable(s.kind) ? 'Open ' : 'Download ') + s.name);
    let thumb = icon(iconOf(s.name), 22);
    if (s.kind === 'image') thumb = `<img alt="" loading="lazy" src="${esc(s.url)}">`;
    else if (s.kind === 'video') thumb = `<video muted playsinline preload="metadata" src="${esc(s.play)}#t=0.1"></video><span class="hx-file-play">${icon('PlayIcon', 12)}</span>`;
    el.innerHTML = `<span class="hx-file-ic">${thumb}</span>`
      + `<span class="hx-file-tx"><span class="hx-file-nm" title="${esc(s.name)}">${nameHtml(s.name)}</span>`
      + `<span class="hx-file-mt">${esc(typeLabel(s.name))}</span></span>`
      + `<a class="hx-file-dl" href="${esc(s.dl)}" download="${esc(s.name)}" title="Download" aria-label="Download ${esc(s.name)}">${icon('Download04Icon', 18)}</a>`;
    const tImg = el.querySelector('.hx-file-ic img, .hx-file-ic video');
    if (tImg) tImg.addEventListener('error', () => { el.querySelector('.hx-file-ic').innerHTML = icon(iconOf(s.name), 22); }, { once: true });
    el._hx = s;
    probe(s.url).then((r) => {
      s.size = r.size;
      const mt = el.querySelector('.hx-file-mt');
      if (!r.ok && !extOf(s.name) && s.attach) { // upstream extracts uploaded archives into a folder
        s.kind = 'folder';
        el.setAttribute('data-kind', 'folder');
        el.setAttribute('aria-label', s.name + ', folder');
        el.querySelector('.hx-file-ic').innerHTML = icon('Folder01Icon', 22);
        el.querySelector('.hx-file-dl').remove();
        mt.textContent = 'Folder';
        return;
      }
      if (!r.ok) { el.classList.add('hx-file--missing'); mt.textContent = typeLabel(s.name) + ' · Unavailable'; return; }
      if (r.size != null) mt.textContent = typeLabel(s.name) + ' · ' + fmtSize(r.size);
    });
    return el;
  }
  function filesRow(cls) {
    const row = document.createElement('div');
    row.className = 'hx-files' + (cls ? ' ' + cls : '');
    return row;
  }
  // Put a card where `anchor` is, merging into an adjacent card row.
  function placeCard(anchor, s, replace) {
    const prev = replace ? anchor.previousElementSibling : null;
    const c = card(s);
    if (prev && prev.classList.contains('hx-files')) { prev.appendChild(c); if (replace) anchor.remove(); return; }
    const row = filesRow();
    row.appendChild(c);
    if (replace) anchor.replaceWith(row); else anchor.after(row);
  }

  // ── inline players: rounded, plus a name / size / download row ───────────
  function mediaRow(s) {
    const row = document.createElement('div');
    row.className = 'hx-media-row';
    row.innerHTML = `<span class="hx-media-ic">${icon(iconOf(s.name), 16)}</span><span class="hx-file-nm" title="${esc(s.name)}">${nameHtml(s.name)}</span>`
      + `<span class="hx-file-mt">${esc(typeLabel(s.name))}</span>`
      + `<a class="hx-file-dl" href="${esc(s.dl)}" download="${esc(s.name)}" title="Download" aria-label="Download ${esc(s.name)}">${icon('Download04Icon', 16)}</a>`;
    probe(s.url).then((r) => { if (r.ok && r.size != null) row.querySelector('.hx-file-mt').textContent = typeLabel(s.name) + ' · ' + fmtSize(r.size); });
    return row;
  }
  function pathFromUrl(u) {
    try {
      const q = new URL(u, location.href).searchParams;
      return q.get('path') || '';
    } catch (_) { return ''; }
  }
  function dressPlayer(m) {
    if (m.closest('.hx-media, .hx-viewer, .hx-file')) return;
    const src = m.getAttribute('src') || '';
    const path = m.getAttribute('data-hx-media') || pathFromUrl(src);
    if (!path) return;
    const s = source(path, sidOf(m));
    if (!/api\/media/.test(src)) { s.url = src.replace(/[?&]inline=1/, ''); s.dl = s.url + (s.url.includes('?') ? '&' : '?') + 'download=1'; }
    const editor = m.closest('.msg-media-editor');
    let box = editor;
    if (!box) {
      box = document.createElement('div');
      m.replaceWith(box);
      box.appendChild(m);
    }
    box.classList.add('hx-media', 'hx-media--' + (m.tagName === 'VIDEO' ? 'video' : 'audio'));
    const meta = box.querySelector('.msg-media-meta');
    (meta || m).after(mediaRow(s));
  }

  // ── MEDIA: /path (with a space; upstream leaves it as text) ───────────────
  const MEDIA_RX = /MEDIA:\s+(\/[^\s)\]]+)/;
  function mediaTokens(body) {
    const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT, {
      acceptNode: (n) => (n.nodeValue.includes('MEDIA:') && MEDIA_RX.test(n.nodeValue)
        && !n.parentElement.closest('pre, code, textarea, [contenteditable], .hx-files'))
        ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT,
    });
    const hits = [];
    while (walker.nextNode()) hits.push(walker.currentNode);
    hits.forEach((node) => {
      const m = node.nodeValue.match(MEDIA_RX);
      if (!m) return;
      const s = source(m[1], sidOf(body));
      let el;
      if (s.kind === 'video' || s.kind === 'audio') {
        el = document.createElement(s.kind);
        Object.assign(el, { controls: true, preload: 'metadata', src: s.play, title: s.name });
        if (s.kind === 'video') el.playsInline = true;
        el.className = 'msg-media-player msg-media-' + s.kind;
      } else if (s.kind === 'image') {
        el = document.createElement('img');
        Object.assign(el, { src: s.url, alt: s.name, loading: 'lazy' });
        el.className = 'msg-media-img';
      } else {
        el = filesRow();
        el.appendChild(card(s));
      }
      el.setAttribute('data-hx-media', m[1]);
      const block = node.parentElement;
      if (block && block.textContent.trim() === m[0].trim() && /^(P|DIV|SPAN|LI)$/.test(block.tagName) && !block.classList.contains('msg-body')) {
        block.replaceWith(el);
      } else {
        const after = node.splitText(node.nodeValue.indexOf(m[0]));
        after.nodeValue = after.nodeValue.slice(m[0].length);
        after.before(el);
      }
    });
  }

  // ── upstream file chrome -> cards ─────────────────────────────────────────
  function upstreamChrome(body) {
    body.querySelectorAll('a.msg-media-link[href*="api/media?"]').forEach((a) => {
      if (a.closest('.hx-files, .pdf-preview-wrap')) return;
      const p = pathFromUrl(a.getAttribute('href'));
      if (p) placeCard(a, source(p, sidOf(a)), true);
    });
    body.querySelectorAll('.pdf-preview-load[data-path]').forEach((d) => placeCard(d, source(d.getAttribute('data-path'), sidOf(d)), true));
  }

  // ── referenced paths / file links in replies ──────────────────────────────
  const PATH_RX = /(?:^|[\s(`'"“‘])(\/(?:[^\s'"`<>()[\]{}|*?]+\/)*[^\s'"`<>()[\]{}|*?/]+\.[A-Za-z0-9]{1,10})(?=$|[\s)`'".,;:!?”’])/g;
  function references(body) {
    if (body.closest('[data-role="user"]')) return;
    const shown = new Set([...body.querySelectorAll('[data-hx-path], [data-hx-media]')].map((e) => e.getAttribute('data-hx-path') || e.getAttribute('data-hx-media')));
    body.querySelectorAll('video[src], audio[src], img[src]').forEach((m) => { const p = pathFromUrl(m.getAttribute('src')); if (p) shown.add(p); });
    const found = [];
    const add = (p) => { if (p && p.startsWith('/') && !shown.has(p) && !found.includes(p) && extOf(p)) found.push(p); };
    body.querySelectorAll('a[href]').forEach((a) => {
      if (a.closest('.hx-files, .hx-media')) return;
      const h = a.getAttribute('href') || '';
      if (/^api\/(media|file\/raw)\?/.test(h) || /^file:\/\//i.test(h)) {
        if (/^file:/i.test(h)) { try { add(decodeURIComponent(new URL(h).pathname)); } catch (_) {} } else add(pathFromUrl(h));
      }
    });
    const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT, {
      acceptNode: (n) => (n.nodeValue.includes('/') && !n.parentElement.closest('pre, textarea, .hx-files, .hx-media, a'))
        ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT,
    });
    while (walker.nextNode() && found.length < 12) {
      for (const m of walker.currentNode.nodeValue.matchAll(PATH_RX)) add(m[1]);
    }
    if (!found.length) return;
    const sid = sidOf(body);
    const key = found.join('\n');
    let row = body.nextElementSibling && body.nextElementSibling.classList.contains('hx-refs') ? body.nextElementSibling : null;
    if (row && row.getAttribute('data-key') === key) return;
    Promise.all(found.map((p) => { const s = source(p, sid); return probe(s.url).then((r) => (r.ok ? s : null)); })).then((list) => {
      list = list.filter(Boolean);
      if (!body.isConnected) return;
      row = body.nextElementSibling && body.nextElementSibling.classList.contains('hx-refs') ? body.nextElementSibling : null;
      if (row && row.getAttribute('data-key') === key) return;
      if (row) row.remove();
      if (!list.length) return;
      row = filesRow('hx-refs');
      row.setAttribute('data-key', key);
      list.forEach((s) => row.appendChild(card(s)));
      body.after(row);
    });
  }

  // ── user uploads ─────────────────────────────────────────────────────────
  const MARKER = /\[Attached files: ([^\]]+)\]\s*$/;
  function attachedPaths(row) {
    let text = '';
    try {
      const i = +row.getAttribute('data-msg-idx');
      const m = S.messages && S.messages[i];
      if (m && m.role === 'user') text = String(m.content || '');
    } catch (_) {}
    if (!MARKER.test(text)) {
      const k = row.getAttribute('data-message-anchor-key') || '';
      try { text = decodeURIComponent(k.split('|').slice(3).join('|')); } catch (_) { text = ''; }
    }
    const m = MARKER.exec(text);
    return m ? m[1].split(/,\s+(?=\/)/).map((x) => x.trim()).filter(Boolean) : [];
  }
  function userFiles(row) {
    const body = row.querySelector('.msg-body');
    if (!body) return;
    let paths = attachedPaths(row);
    const up = row.querySelector('.msg-files');
    if (!paths.length && up) {
      paths = [...up.children].map((c) => {
        const m = c.matches('img, video, audio') ? c : c.querySelector('img, video, audio');
        if (m) return pathFromUrl(m.getAttribute('src'));
        return (c.textContent || '').trim();
      }).filter(Boolean);
    }
    if (up) up.classList.add('hx-hidden');
    if (!paths.length) return;
    const key = paths.join('\n');
    const have = row.querySelector(':scope .hx-files.hx-uploads');
    if (have && have.getAttribute('data-key') === key) return;
    if (have) have.remove();
    const sid = row.getAttribute('data-session-id') || sidOf(row);
    const box = filesRow('hx-uploads');
    box.setAttribute('data-key', key);
    paths.forEach((p) => box.appendChild(card(source(p, sid, { attach: true }))));
    (up || body).after(box);
  }

  // ── viewer ───────────────────────────────────────────────────────────────
  let V = null, lastFocus = null, current = null;
  function viewer() {
    if (V) return V;
    V = document.createElement('div');
    V.className = 'hx-viewer';
    V.hidden = true;
    V.setAttribute('role', 'dialog');
    V.setAttribute('aria-modal', 'true');
    V.innerHTML = '<div class="hx-v-scrim"></div><div class="hx-v-sheet">'
      + `<header class="hx-v-head"><button type="button" class="hx-back hx-v-x" aria-label="Back">${icon('ArrowLeft01Icon', 18)}<span>Back</span></button>`
      + '<span class="hx-v-ic"></span><div class="hx-v-tt"><div class="hx-v-nm"></div><div class="hx-v-mt"></div></div>'
      + `<a class="hx-v-btn hx-v-dl" title="Download">${icon('Download04Icon', 18)}</a></header>`
      + '<div class="hx-v-body"></div></div>';
    document.body.appendChild(V);
    V.querySelector('.hx-v-scrim').addEventListener('click', close);
    V.querySelector('.hx-v-x').addEventListener('click', close);
    swipe(V.querySelector('.hx-v-sheet'));
    return V;
  }
  function close() {
    if (!V || V.hidden) return;
    V.querySelectorAll('video, audio').forEach((m) => { try { m.pause(); } catch (_) {} });
    V.querySelector('.hx-v-body').innerHTML = '';
    V.hidden = true;
    document.documentElement.classList.remove('hx-viewer-open');
    current = null;
    if (lastFocus && lastFocus.isConnected) try { lastFocus.focus({ preventScroll: true }); } catch (_) {}
  }
  // Swipe down to dismiss (touch), unless the gesture starts on a media control or a scrolled body.
  function swipe(sheet) {
    let y0 = null, dy = 0;
    sheet.addEventListener('touchstart', (e) => {
      const b = sheet.querySelector('.hx-v-body');
      const scroller = e.target.closest('.hx-v-scroll, .hx-v-pages');
      if (e.touches.length !== 1 || e.target.closest('video, audio, iframe, input, .hx-v-img.zoomed') || (scroller && scroller.scrollTop > 0) || (b && b.scrollTop > 0)) { y0 = null; return; }
      y0 = e.touches[0].clientY; dy = 0;
      sheet.style.transition = 'none';
    }, { passive: true });
    sheet.addEventListener('touchmove', (e) => {
      if (y0 == null) return;
      dy = Math.max(0, e.touches[0].clientY - y0);
      sheet.style.transform = dy ? `translateY(${dy}px)` : '';
    }, { passive: true });
    sheet.addEventListener('touchend', () => {
      if (y0 == null) return;
      sheet.style.transition = '';
      sheet.style.transform = '';
      if (dy > 90) close();
      y0 = null;
    });
  }
  function csvTable(text) {
    const rows = [];
    let row = [], f = '', q = false;
    const delim = text.indexOf('\t') > -1 && text.indexOf(',') === -1 ? '\t' : ',';
    for (let i = 0; i < text.length && rows.length < 2000; i++) {
      const c = text[i];
      if (q) { if (c === '"' && text[i + 1] === '"') { f += '"'; i++; } else if (c === '"') q = false; else f += c; }
      else if (c === '"') q = true;
      else if (c === delim) { row.push(f); f = ''; }
      else if (c === '\n' || c === '\r') { if (c === '\r' && text[i + 1] === '\n') i++; row.push(f); rows.push(row); row = []; f = ''; }
      else f += c;
    }
    if (f || row.length) { row.push(f); rows.push(row); }
    const [head, ...rest] = rows;
    if (!head) return '<p class="hx-v-note">Empty file</p>';
    return '<div class="hx-v-scroll hx-v-table"><table><thead><tr>' + head.map((h) => `<th>${esc(h)}</th>`).join('') + '</tr></thead><tbody>'
      + rest.map((r) => '<tr>' + r.map((c) => `<td>${esc(c)}</td>`).join('') + '</tr>').join('') + '</tbody></table></div>';
  }
  function tooBig(s, why) {
    return `<div class="hx-v-empty">${icon(iconOf(s.name), 40)}<p>${esc(why)}</p><a class="hx-v-cta" href="${esc(s.dl)}" download="${esc(s.name)}">${icon('Download04Icon', 16)} Download</a></div>`;
  }
  function textView(s, body) {
    body.innerHTML = '<div class="hx-v-note">Loading…</div>';
    const go = () => fetch(s.url, { credentials: 'same-origin' }).then((r) => { if (!r.ok) throw new Error(r.status); return r.text(); }).then((t) => {
      if (current !== s) return;
      const e = extOf(s.name);
      if ((e === 'md' || e === 'markdown') && typeof window.renderMd === 'function') {
        body.innerHTML = '<div class="hx-v-scroll"><div class="hx-v-md msg-body"></div></div>';
        body.querySelector('.hx-v-md').innerHTML = window.renderMd(t);
      } else if (e === 'csv' || e === 'tsv') {
        body.innerHTML = csvTable(t);
      } else {
        if (e === 'json') { try { t = JSON.stringify(JSON.parse(t), null, 2); } catch (_) {} }
        body.innerHTML = '<div class="hx-v-scroll"><pre class="hx-v-code"><code></code></pre></div>';
        body.querySelector('code').textContent = t;
      }
    }).catch(() => { if (current === s) body.innerHTML = tooBig(s, 'This file could not be loaded.'); });
    if (s.size != null) return s.size > TEXT_CAP ? (body.innerHTML = tooBig(s, `Too large to preview (${fmtSize(s.size)}).`)) : go();
    probe(s.url).then((r) => { if (current !== s) return; s.size = r.size; if (r.size > TEXT_CAP) body.innerHTML = tooBig(s, `Too large to preview (${fmtSize(r.size)}).`); else go(); });
  }
  // The PDF.js build upstream already uses (loaded on demand from the same CDN).
  function pdfjs() {
    if (window._pdfjsLib) return Promise.resolve(window._pdfjsLib);
    return new Promise((res, rej) => {
      window.addEventListener('pdfjs-ready', () => res(window._pdfjsLib), { once: true });
      if (!window.__hxPdfjsLoading) {
        window.__hxPdfjsLoading = true;
        const src = 'https://cdn.jsdelivr.net/npm/pdfjs-dist@4.9.155/build/pdf.min.mjs';
        const wk = 'https://cdn.jsdelivr.net/npm/pdfjs-dist@4.9.155/build/pdf.worker.min.mjs';
        const blob = new Blob([`import*as p from'${src}';p.GlobalWorkerOptions.workerSrc='${wk}';window._pdfjsLib=p;window._pdfjsReady=true;window.dispatchEvent(new Event('pdfjs-ready'));`], { type: 'application/javascript' });
        const sc = document.createElement('script');
        sc.type = 'module';
        sc.src = URL.createObjectURL(blob);
        document.head.appendChild(sc);
      }
      setTimeout(() => rej(new Error('pdfjs timeout')), 15000);
    });
  }
  // Every PDF renders with PDF.js (an iframe shows one page or nothing on iPhones): one page per screen,
  // fitted to it, snapping page to page as you scroll or swipe, with a "3 / 18" counter. Only pages
  // near the view keep a canvas, so a long PDF doesn't run a phone out of memory.
  function pdfView(s, body) {
    body.innerHTML = '<div class="hx-v-pages" tabindex="0"><div class="hx-v-note">Loading…</div></div><div class="hx-v-pgn" hidden></div>';
    const wrap = body.querySelector('.hx-v-pages'), pgn = body.querySelector('.hx-v-pgn');
    Promise.all([pdfjs(), fetch(s.url, { credentials: 'same-origin' }).then((r) => { if (!r.ok) throw new Error(r.status); return r.arrayBuffer(); })])
      .then(([lib, buf]) => lib.getDocument({ data: buf, isEvalSupported: false }).promise)
      .then((pdf) => {
        if (current !== s) return;
        const n = pdf.numPages;
        wrap.innerHTML = '';
        const slots = [];
        for (let i = 1; i <= n; i++) {
          const d = document.createElement('div');
          d.className = 'hx-v-pg';
          d.dataset.n = i;
          d.setAttribute('aria-label', `Page ${i} of ${n}`);
          wrap.appendChild(d);
          slots.push(d);
        }
        const want = new Set(); // pages in or near the view
        const drawn = new Map(); // page -> the size it was drawn for
        async function draw(d) {
          const i = +d.dataset.n, W = d.clientWidth - 24, H = d.clientHeight - 24;
          if (W <= 0 || H <= 0 || drawn.get(i) === W + 'x' + H) return;
          drawn.set(i, W + 'x' + H);
          const page = await pdf.getPage(i);
          const base = page.getViewport({ scale: 1 });
          const fit = Math.min(W / base.width, H / base.height);
          const dpr = Math.min(window.devicePixelRatio || 1, 2);
          const vp = page.getViewport({ scale: fit * dpr });
          const c = document.createElement('canvas');
          c.className = 'hx-v-page';
          c.width = Math.floor(vp.width); c.height = Math.floor(vp.height);
          c.style.width = Math.floor(vp.width / dpr) + 'px'; c.style.height = Math.floor(vp.height / dpr) + 'px';
          await page.render({ canvasContext: c.getContext('2d'), viewport: vp }).promise;
          if (current !== s || !want.has(i) || drawn.get(i) !== W + 'x' + H) { c.width = c.height = 0; return; }
          d.querySelectorAll('canvas').forEach((x) => { x.width = x.height = 0; x.remove(); });
          d.appendChild(c);
        }
        function drop(d) {
          const i = +d.dataset.n;
          drawn.delete(i);
          d.querySelectorAll('canvas').forEach((x) => { x.width = x.height = 0; x.remove(); }); // frees the bitmap now
        }
        const io = new IntersectionObserver((es) => es.forEach((e) => {
          const i = +e.target.dataset.n;
          if (e.isIntersecting) { want.add(i); draw(e.target).catch(() => {}); } else { want.delete(i); drop(e.target); }
        }), { root: wrap, rootMargin: '100% 0px' });
        slots.forEach((d) => io.observe(d));
        const ro = new ResizeObserver(() => slots.forEach((d) => { if (want.has(+d.dataset.n)) draw(d).catch(() => {}); }));
        ro.observe(wrap);
        const done = new MutationObserver(() => { if (!wrap.isConnected) { io.disconnect(); ro.disconnect(); done.disconnect(); pdf.destroy(); } });
        done.observe(body, { childList: true });
        const count = () => { pgn.textContent = `${Math.min(n, Math.round(wrap.scrollTop / Math.max(1, wrap.clientHeight)) + 1)} / ${n}`; };
        pgn.hidden = false;
        count();
        wrap.addEventListener('scroll', count, { passive: true });
        wrap.focus({ preventScroll: true });
      }).catch(() => { if (current === s) body.innerHTML = tooBig(s, 'This PDF could not be previewed.'); });
  }
  function open(s) {
    const v = viewer();
    lastFocus = document.activeElement;
    current = s;
    v.setAttribute('data-kind', s.kind);
    v.setAttribute('aria-label', s.name);
    v.querySelector('.hx-v-ic').innerHTML = icon(iconOf(s.name), 20);
    v.querySelector('.hx-v-nm').innerHTML = nameHtml(s.name);
    v.querySelector('.hx-v-nm').title = s.name;
    v.querySelector('.hx-v-mt').textContent = typeLabel(s.name) + (s.size != null ? ' · ' + fmtSize(s.size) : '');
    const dl = v.querySelector('.hx-v-dl');
    dl.href = s.dl; dl.setAttribute('download', s.name); dl.setAttribute('aria-label', 'Download ' + s.name);
    const body = v.querySelector('.hx-v-body');
    body.innerHTML = '';
    if (s.kind === 'image') {
      body.innerHTML = `<div class="hx-v-img"><img alt="${esc(s.name)}" src="${esc(s.url)}"></div>`;
      const box = body.firstChild;
      box.addEventListener('click', (e) => {
        const img = box.querySelector('img');
        const r = img.getBoundingClientRect();
        const fx = (e.clientX - r.left) / r.width, fy = (e.clientY - r.top) / r.height;
        box.classList.toggle('zoomed');
        if (box.classList.contains('zoomed')) {
          box.scrollLeft = fx * box.scrollWidth - box.clientWidth / 2;
          box.scrollTop = fy * box.scrollHeight - box.clientHeight / 2;
        }
      });
    } else if (s.kind === 'video') {
      body.innerHTML = `<video class="hx-v-video" controls playsinline preload="metadata" src="${esc(s.play)}"></video>`;
    } else if (s.kind === 'audio') {
      body.innerHTML = `<div class="hx-v-audio"><span class="hx-v-art">${icon('FileAudioIcon', 44)}</span><audio controls preload="metadata" src="${esc(s.play)}"></audio></div>`;
    } else if (s.kind === 'pdf') pdfView(s, body);
    else textView(s, body);
    v.hidden = false;
    document.documentElement.classList.add('hx-viewer-open');
    v.querySelector('.hx-v-x').focus({ preventScroll: true });
  }
  window.hxOpenFile = (path, sid) => open(source(path, sid || sidOf(null)));

  // ── events ───────────────────────────────────────────────────────────────
  function activate(c, e) {
    const s = c._hx;
    if (!s) return;
    if (e.target.closest('.hx-file-dl')) return; // the download link does its own thing
    e.preventDefault();
    const dl = c.querySelector('.hx-file-dl');
    if (viewable(s.kind) && !c.classList.contains('hx-file--missing')) open(s);
    else if (dl && s.kind !== 'folder') dl.click();
  }
  document.addEventListener('click', (e) => {
    const c = e.target.closest && e.target.closest('.hx-file');
    if (c) activate(c, e);
  });
  document.addEventListener('keydown', (e) => {
    if (V && !V.hidden) {
      if (e.key === 'Escape') { e.preventDefault(); e.stopImmediatePropagation(); close(); return; }
      if (e.key === 'Tab') { // keep focus inside the dialog
        const f = [...V.querySelectorAll('a[href], button, video, audio, iframe, [tabindex]')].filter((x) => x.offsetParent);
        if (f.length && !V.contains(document.activeElement)) { e.preventDefault(); f[0].focus(); }
      }
      return;
    }
    const c = e.target.closest && e.target.closest('.hx-file');
    if (c && (e.key === 'Enter' || e.key === ' ') && e.target === c) activate(c, e);
  }, true);

  // ── wiring ───────────────────────────────────────────────────────────────
  function scan(root) {
    if (!root) return;
    root.querySelectorAll('.msg-row[data-role="user"]').forEach(userFiles);
    root.querySelectorAll('.msg-row:not([data-role="user"]) .msg-body').forEach((b) => {
      if (b.closest('.hx-viewer')) return;
      mediaTokens(b);
      upstreamChrome(b);
      b.querySelectorAll('video.msg-media-video, audio.msg-media-audio, video.msg-media-player, audio.msg-media-player').forEach(dressPlayer);
      references(b);
    });
  }
  let queued = false;
  const mo = new MutationObserver(() => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => { queued = false; scan(document.getElementById('messages')); });
  });
  function start() {
    const msgs = document.getElementById('messages');
    scan(msgs);
    if (msgs) mo.observe(msgs, { childList: true, subtree: true, characterData: true });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
