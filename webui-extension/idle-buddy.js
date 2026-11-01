/* Hermes — animated idle buddy on the empty state (plain JS, no build step).
 * Plays 24fps alpha video clips that all start and end on the same frame, so they chain with no
 * jump: mostly the calm base loop, with a random variant (tilt, look around, happy bounce) every few
 * loops. Two stacked <video>s: the next clip is preloaded and swapped in on `ended`, so there is no
 * blank frame between clips. Safari/iOS get HEVC-with-alpha (.mov), everything else VP9-alpha
 * (.webm). The CSS poster stays underneath; reduced-motion users only ever see the poster.
 * Clips frame the rest pose exactly like mascot-512.png plus 15% headroom per side for motion, so
 * the video is drawn 15% past the logo box: same size as the still, nothing clipped.
 * Tabbie plays on the empty state; any element with data-buddy="<set>" plays that set (Zoetie, motion.js).
 */
(function () {
  'use strict';
  const BASE = 'base';
  const VARIANTS = ['tilt', 'look', 'joy'];
  const safari = /Apple/.test(navigator.vendor || '');
  // Served by /api/media (byte ranges + video MIME, which Safari needs); ~/hermes/brand is an
  // allowed media root. Each clip is fetched once into a blob URL so loops never re-request it.
  // Clip sets: Tabbie on the empty state; others (Zoetie, motion.js) on any element with data-buddy.
  const SETS = {
    tabbie: { dir: `${(window.HX_LOCAL || {}).hermes || ''}/brand/avatar/idle/web`, v: '4' },
    zoetie: { dir: `${(window.HX_LOCAL || {}).hermes || ''}/brand/avatar/motion/web`, v: '1' },
  };
  const LOGOS = '.empty-logo, [data-buddy]';
  const setOf = (logo) => (SETS[logo.dataset.buddy] ? logo.dataset.buddy : 'tabbie');
  const url = (set, name) => `api/media?path=${encodeURIComponent(`${SETS[set].dir}/${name}.${safari ? 'mov' : 'webm'}`)}&v=${SETS[set].v}`;
  // Fetched lazily (only once the empty state is actually on screen), once per clip per page.
  const blobs = {};   // set/name -> blob URL, once fetched
  const loading = {}; // set/name -> Promise<url> (blob URL, or the plain URL if the fetch failed)
  function get(set, name) {
    const k = `${set}/${name}`;
    if (!loading[k]) {
      loading[k] = fetch(url(set, name), { credentials: 'same-origin' })
        .then((r) => (r.ok ? r.blob() : Promise.reject()))
        .then((b) => (blobs[k] = URL.createObjectURL(b)), () => { delete loading[k]; return url(set, name); });
    }
    return loading[k];
  }
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');

  function picker(set) {
  let loopsSinceVariant = 0, lastVariant = '';
  return function nextClip() {
    // Base loop 2–4 times, then one variant (never the same one twice in a row).
    if (loopsSinceVariant >= 2 && Math.random() < 0.5 + (loopsSinceVariant - 2) * 0.25) {
      loopsSinceVariant = 0;
      const ready = (v) => !!blobs[`${set}/${v}`];
      const pool = VARIANTS.filter((v) => v !== lastVariant && ready(v));
      if (!pool.length) return BASE;
      lastVariant = pool[Math.floor(Math.random() * pool.length)];
      return lastVariant;
    }
    loopsSinceVariant++;
    return BASE;
  };
  }

  function makeVideo() {
    const v = document.createElement('video');
    v.muted = true; v.playsInline = true; v.preload = 'auto';
    v.setAttribute('muted', ''); v.setAttribute('playsinline', ''); v.setAttribute('aria-hidden', 'true');
    v.disablePictureInPicture = true;
    // object-fit:fill, not contain: the clips are square, and Safari sometimes lays out a freshly
    // swapped-in clip with the default 300x150 intrinsic size, so `contain` drew it at half size.
    v.style.cssText = 'position:absolute;inset:-15%;width:130%;height:130%;object-fit:fill;'
      + 'pointer-events:none;background:transparent;opacity:0';
    return v;
  }

  function mount(logo) {
    if (logo.querySelector('video.hx-idle') || reduced.matches) return;
    const set = setOf(logo), nextClip = picker(set), base = `${set}/${BASE}`;
    const a = makeVideo(), b = makeVideo();
    a.className = b.className = 'hx-idle';
    logo.append(a, b);
    let cur = a, nxt = b;
    // Each clip's src is set only once its blob is ready, so the <video> never fetches it a second time.
    const queue = (v) => { get(set, nextClip()).then((u) => { v.src = u; v.load(); }); };
    get(set, BASE).then((u) => { cur.src = u; cur.play().catch(() => {}); queue(nxt); });
    const show = (v) => { v.style.opacity = '1'; logo.classList.add('hx-idle-live'); };
    cur.addEventListener('playing', () => show(cur), { once: true });
    function onEnded() {
      // Next clip not decoded yet (no frame / size): loop the current one again instead of
      // swapping in an empty or mis-sized video.
      if (!(nxt.readyState >= 2 && nxt.videoWidth)) { cur.currentTime = 0; cur.play().catch(() => {}); return; }
      const done = cur;
      cur = nxt; nxt = done;
      cur.currentTime = 0;
      const p = cur.play();
      const swap = () => { cur.style.opacity = '1'; done.style.opacity = '0'; queue(done); };
      if (p && p.then) p.then(swap, () => { done.currentTime = 0; done.play(); cur = done; nxt = cur === a ? b : a; });
      else swap();
    }
    // A clip that fails to load falls back to the base loop.
    [a, b].forEach((v) => v.addEventListener('error', () => { if (v === nxt && blobs[base] && v.src !== blobs[base]) { v.src = blobs[base]; v.load(); } }));
    a.addEventListener('ended', function () { if (this === cur) onEnded(); });
    b.addEventListener('ended', function () { if (this === cur) onEnded(); });
    // autoplay muted+inline is allowed (started above once the base clip is in); if blocked, the poster stays
  }

  // The empty state also flashes during boot before the saved conversation is restored, so only
  // count it once boot is done and it has stayed up a moment.
  const booted = () => { try { return typeof S === 'undefined' || !!S._bootReady; } catch (_) { return true; } };
  const shown = (el) => el.getClientRects().length > 0 && booted();
  let settle = 0;
  function scan() {
    if (reduced.matches) return;
    const logos = [...document.querySelectorAll(LOGOS)].filter((l) => shown(l) && !l.querySelector('video.hx-idle'));
    if (!logos.length || settle) return; // hidden empty state (a conversation is open): fetch/play nothing
    settle = setTimeout(() => {
      settle = 0;
      document.querySelectorAll(LOGOS).forEach((logo) => {
        if (shown(logo) && !logo.querySelector('video.hx-idle')) { const set = setOf(logo); [BASE, ...VARIANTS].forEach((n) => get(set, n)); mount(logo); }
      });
    }, 250);
  }
  let queued = false;
  const scanSoon = () => { if (!queued) { queued = true; requestAnimationFrame(() => { queued = false; scan(); }); } };

  document.addEventListener('visibilitychange', () => {
    document.querySelectorAll('video.hx-idle').forEach((v) => {
      if (document.hidden) v.pause(); else if (v.style.opacity === '1') v.play().catch(() => {});
    });
  });

  function start() {
    scan();
    // The empty state is shown/hidden via its style, so watch attributes too (rAF-batched).
    new MutationObserver(scanSoon).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['style', 'class'] });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
