/* Hermes — phone / home-screen app polish (plain JS, no build step). Pairs with pwa-polish.css.
 * hx-kb: upstream measures the keyboard as innerHeight - visualViewport.height, which in the iOS
 * home-screen app is non-zero even with no keyboard up, leaving a big empty band under the
 * composer. Only honour it while a text field is focused and the viewport has really shrunk.
 * hx-scrolled: the title bar gets its frosted fill + hairline only once chat scrolls under it.
 */
(function () {
  'use strict';
  const root = document.documentElement;
  const editable = (el) => !!el && (el.tagName === 'TEXTAREA' || el.isContentEditable
    || (el.tagName === 'INPUT' && !/^(button|checkbox|radio|range|file|submit|reset|color)$/i.test(el.type)));

  function syncKb() {
    const vv = window.visualViewport;
    const shrunk = vv ? innerHeight - vv.height > 120 : false;
    root.classList.toggle('hx-kb', editable(document.activeElement) && shrunk);
  }
  addEventListener('focusin', () => setTimeout(syncKb, 50));
  addEventListener('focusout', () => setTimeout(syncKb, 50));
  if (window.visualViewport) visualViewport.addEventListener('resize', syncKb);

  let scroller = null;
  function syncScroll() { root.classList.toggle('hx-scrolled', !!scroller && scroller.scrollTop > 4); }
  function bindScroller() {
    const el = document.getElementById('messages');
    if (el === scroller) return;
    if (scroller) scroller.removeEventListener('scroll', syncScroll);
    scroller = el;
    if (scroller) scroller.addEventListener('scroll', syncScroll, { passive: true });
    syncScroll();
  }
  function start() {
    syncKb(); bindScroller();
    new MutationObserver(() => { bindScroller(); syncScroll(); }).observe(document.body, { childList: true, subtree: false });
    setInterval(bindScroller, 2000);
  }
  // iOS home-screen app: navigating to a download (same window or a new one) gets stuck — Quick Look
  // with no way back, or the app shell reopening on "Session not available". Never navigate: fetch
  // the file, then hand it to the iOS share sheet (Save Video / Save Image / Save to Files) from a
  // second tap, since iOS needs a fresh tap to open the sheet after a download.
  const iosStandalone = navigator.standalone === true
    || (/iP(hone|ad|od)/.test(navigator.userAgent) && matchMedia('(display-mode: standalone)').matches);
  let dlSheet = null;
  function closeSheet() { if (dlSheet) { dlSheet.remove(); dlSheet = null; } }
  function sheet(name) {
    closeSheet();
    const el = document.createElement('div');
    el.className = 'hx-dl-sheet';
    el.innerHTML = '<div class="hx-dl-card" role="dialog" aria-live="polite"><div class="hx-dl-name"></div>'
      + '<div class="hx-dl-state">Preparing…</div><div class="hx-dl-bar"><i></i></div>'
      + '<div class="hx-dl-row"><button type="button" class="hx-dl-cancel">Cancel</button>'
      + '<button type="button" class="hx-dl-save" disabled>Save</button></div></div>';
    el.querySelector('.hx-dl-name').textContent = name;
    el.addEventListener('click', (e) => { if (e.target === el || e.target.closest('.hx-dl-cancel')) closeSheet(); });
    document.body.appendChild(el);
    dlSheet = el;
    return el;
  }
  async function fetchFile(url, el, name) {
    const r = await fetch(url, { credentials: 'same-origin' });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    // A wrong URL falls through to the app shell: never save that as the user's file.
    if (/text\/html/.test(r.headers.get('content-type') || '') && !/\.html?$/i.test(name)) throw new Error('got the app page');
    const total = +r.headers.get('content-length') || 0;
    let type = (r.headers.get('content-type') || 'application/octet-stream').split(';')[0];
    if (type === 'application/octet-stream') { // generic: name it so iOS offers the right apps
      const ext = (name.split('.').pop() || '').toLowerCase();
      type = { md: 'text/markdown', txt: 'text/plain', json: 'application/json', csv: 'text/csv', pdf: 'application/pdf',
        mp4: 'video/mp4', mov: 'video/quicktime', mp3: 'audio/mpeg', wav: 'audio/wav', png: 'image/png',
        jpg: 'image/jpeg', jpeg: 'image/jpeg', gif: 'image/gif', webp: 'image/webp', zip: 'application/zip' }[ext] || type;
    }
    const parts = []; let got = 0;
    const rd = r.body && r.body.getReader ? r.body.getReader() : null;
    if (!rd) return new File([await r.blob()], name, { type });
    for (;;) {
      const { done, value } = await rd.read();
      if (done) break;
      parts.push(value); got += value.length;
      if (total && el.isConnected) el.querySelector('.hx-dl-bar i').style.width = `${Math.round(got / total * 100)}%`;
    }
    return new File(parts, name, { type });
  }
  async function download(href, name) {
    const el = sheet(name);
    const save = el.querySelector('.hx-dl-save'), state = el.querySelector('.hx-dl-state');
    let file;
    try { file = await fetchFile(href, el, name); } catch (err) {
      if (el.isConnected) state.textContent = "Couldn't download this file. Try again.";
      return;
    }
    if (!el.isConnected) return; // cancelled
    el.querySelector('.hx-dl-bar i').style.width = '100%';
    const canShare = navigator.canShare && navigator.canShare({ files: [file] });
    state.textContent = canShare ? 'Ready · tap Save to keep it' : 'Ready';
    save.disabled = false;
    save.onclick = async () => {
      if (canShare) {
        try { await navigator.share({ files: [file], title: name }); closeSheet(); } catch (e) {
          if (e && e.name === 'AbortError') return; // user closed the share sheet: keep ours open
          state.textContent = "Couldn't open the share sheet.";
        }
      } else { // no file sharing: show it in-page; long-press saves on iOS
        const u = URL.createObjectURL(file);
        const media = /^video\//.test(file.type) ? `<video src="${u}" controls playsinline></video>`
          : /^image\//.test(file.type) ? `<img src="${u}" alt="">` : '';
        state.innerHTML = media || 'Saved in memory, but this device can\'t share files.';
      }
    };
  }
  if (iosStandalone) {
    document.addEventListener('click', (e) => {
      const a = e.target.closest && e.target.closest('a[href]');
      if (!a || e.defaultPrevented) return;
      const href = a.getAttribute('href') || '';
      if (!a.hasAttribute('download') && !/[?&]download=1\b/.test(href)) return;
      e.preventDefault();
      e.stopPropagation();
      const url = a.href; // resolved against <base href>, not the /session/<id> path
      const name = a.getAttribute('download') || decodeURIComponent((url.match(/[?&]path=([^&]+)/) || [, 'file'])[1]).split('/').pop();
      download(url, name);
    }, true);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
