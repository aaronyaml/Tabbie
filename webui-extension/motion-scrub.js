/* Tabbie — Motion studio scrubber (plain JS, no build step). Used by motion-studio.js on the Audio and
 * Mapping steps: real playback, scrubbing, zoom and the analysis markers explained.
 *
 *  Playback  play/pause (Space), playhead + m:ss.mmm readout, optional loop of a range.
 *  Scrub     tap/click the waveform = seek; drag the ruler or the playhead = scrub; ←/→ = 1 frame (30 fps),
 *            Shift+←/→ = previous/next marker. Double-tap, "Mark here" or M adds a keypoint (Mapping only).
 *            Mouse drag across the waveform selects a range to loop.
 *            onRange([startMs, endMs] | null) reports it (Mapping offers "use only this part").
 *  Zoom      whole track down to a 1 s window: −/+ and slider, ⌘/Ctrl+wheel and trackpad pinch, two-finger
 *            pinch on phones; one finger / horizontal wheel pans. Overview strip shows the visible window.
 *            Ruler ticks adapt (seconds → 100 ms → frames); markers spread out and get labels.
 *  Drawing   ruler + waveform on one viewport-sized canvas, redrawn on scroll/zoom only (multi-resolution
 *            peaks, so it stays sharp when zoomed). requestAnimationFrame moves the playhead only.
 *
 *  Trim      set({trim: {startMs, endMs}, snapPts: [ms…]}) shows a selection window (Audio step, before
 *            analysis): drag its middle to move it at a fixed length, drag an edge handle to resize it.
 *            Playback stays inside the window (Loop repeats it). Snap (opts.snap() or Shift held) pulls
 *            the moving edge to the nearest snap point within 12 px.
 *
 * window.hxScrub.create(opts) -> scrubber; opts: { onMark(ms), onPin(id), onTrim(trim, how, done), onMeta(durMs), onRange(r), snap(), fps }.
 *   s.el                     the root element (persistent: move it into a slot after each studio render)
 *   s.set({src, durationMs, peaks, markers, pins, markMode, trim, snapPts})
 *   s.attach(slot) / s.key(e) / s.toggle() / s.pause() / s.time() / s.pxPerMs() / s.destroy()
 * peaks: {coarse: Float32Array (whole track), fine: Float32Array, finePerSec: n, durationMs}
 * markers: [{kind: beat|downbeat|word|sound|drop|section, id, ms, label, short, detail}]
 */
(function () {
  'use strict';
  if (window.hxScrub) return;

  const icon = (n, s) => (typeof window.hxIcon === 'function' ? window.hxIcon(n, s) : '');
  const esc = (t) => String(t == null ? '' : t).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const fmtT = (ms) => { ms = Math.max(0, ms); const s = Math.floor(ms / 1000); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}.${String(Math.floor(ms % 1000)).padStart(3, '0')}`; };
  const RULER = 22, WAVE = 92, MIN_WIN = 1000;
  const KINDS = {
    downbeat: ['Downbeats', 'first beat of each bar; the strongest cut points'],
    beat: ['Beats', 'where the pulse falls; good cut points'],
    word: ['Words', 'a sung or spoken word starts (purple, top)'],
    sound: ['Hits', 'a distinct sound: kick, 808, stab, impact (orange)'],
    drop: ['Drops', 'the track drops or the energy jumps (red)'],
    section: ['Sections', 'a new part of the song, or the vocals come in'],
  };
  const ORDER = ['downbeat', 'beat', 'word', 'sound', 'drop', 'section'];
  const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;

  function create(opts = {}) {
    const fps = opts.fps || 30;
    const el = document.createElement('div');
    el.className = 'hsc';
    el.innerHTML = `
      <div class="hsc-bar">
        <button type="button" class="hsc-play" aria-label="Play">${icon('PlayIcon', 22)}</button>
        <span class="hsc-time">0:00.000</span>
        <span class="hsc-hint"></span>
        <span class="hsc-sp"></span>
        <button type="button" class="hsc-btn hsc-loop" aria-pressed="false" title="Loop the selected range (or 2 s around the playhead)">${icon('RefreshIcon', 16)}<span>Loop</span></button>
        <button type="button" class="hsc-btn hsc-markb" hidden title="Add a keypoint at the playhead (M)">${icon('Add01Icon', 16)}<span>Mark here</span></button>
        <span class="hsc-zoom"><button type="button" class="hsc-zb" data-z="-1" aria-label="Zoom out">−</button><input type="range" min="0" max="1000" value="0" aria-label="Zoom"><button type="button" class="hsc-zb" data-z="1" aria-label="Zoom in">+</button></span>
      </div>
      <div class="hsc-legend"></div>
      <div class="hsc-view">
        <canvas class="hsc-cv"></canvas>
        <div class="hsc-scroll"><div class="hsc-content">
          <div class="hsc-rul" title="Drag to scrub"></div>
          <div class="hsc-mks"></div><div class="hsc-pins"></div>
          <i class="hsc-loopr" hidden></i>
          <div class="hsc-trim" hidden><i class="hsc-dim l"></i><i class="hsc-dim r"></i><b class="hsc-tw" title="Drag to move the section"><i class="hsc-th l" title="Drag to change the start"><span class="hsc-grip"></span></i><i class="hsc-th r" title="Drag to change the end"><span class="hsc-grip"></span></i></b></div>
          <i class="hsc-ph"><b class="hsc-ph-h"></b></i>
        </div></div>
        <div class="hsc-tip" hidden></div>
      </div>
      <div class="hsc-ovw"><canvas class="hsc-ov"></canvas><i class="hsc-ov-win"></i><i class="hsc-ov-ph"></i></div>`;
    const $ = (s) => el.querySelector(s);
    const cv = $('.hsc-cv'), scroll = $('.hsc-scroll'), content = $('.hsc-content'), ph = $('.hsc-ph'), tip = $('.hsc-tip');
    const ov = $('.hsc-ov'), ovWin = $('.hsc-ov-win'), ovPh = $('.hsc-ov-ph'), slider = $('.hsc-zoom input'), timeEl = $('.hsc-time'), playB = $('.hsc-play');
    const audio = new Audio();
    audio.preload = 'auto';
    audio.setAttribute('playsinline', '');

    let D = { src: '', durationMs: 0, peaks: null, markers: [], pins: [], markMode: false, trim: null, snapPts: [] };
    const MIN_TRIM = 500;
    let lastMk = '', lastMarkers = null;
    let z = 1, viewW = 0, raf = 0, drawQ = false, loop = false, range = null, pendingSeek = null, lastTap = null, tipT = 0;
    const dur = () => D.durationMs || (audio.duration ? audio.duration * 1000 : 0);
    const zMax = () => Math.max(1, dur() / MIN_WIN);
    const contentW = () => viewW * z;
    const pxPerMs = () => (dur() ? contentW() / dur() : 0);
    const now = () => (pendingSeek != null ? pendingSeek : audio.currentTime * 1000);
    const win = () => { const d = dur(), w = contentW() || 1; return [scroll.scrollLeft / w * d, (scroll.scrollLeft + viewW) / w * d]; };

    // ── layout + drawing ──
    function layout() {
      const w = scroll.clientWidth;
      if (!w) return;
      const t0 = win()[0];
      viewW = w;
      content.style.width = `${Math.round(contentW())}px`;
      scroll.scrollLeft = dur() ? (t0 / dur()) * contentW() : 0;
      draw();
      paintPh();
      paintTrim();
    }
    function schedule() { if (!drawQ) { drawQ = true; requestAnimationFrame(() => { drawQ = false; draw(); }); } }
    function draw() {
      const dpr = devicePixelRatio || 1, H = RULER + WAVE;
      if (!viewW) return;
      if (cv.width !== Math.round(viewW * dpr) || cv.height !== Math.round(H * dpr)) { cv.width = Math.round(viewW * dpr); cv.height = Math.round(H * dpr); }
      const g = cv.getContext('2d');
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
      g.clearRect(0, 0, viewW, H);
      const css = getComputedStyle(el);
      const ink = css.getPropertyValue('--hsc-wave').trim() || '#3054FD';
      const line = css.getPropertyValue('--hsc-line').trim() || 'rgba(127,127,127,.35)';
      const text2 = css.getPropertyValue('--hsc-text2').trim() || '#8a8a8e';
      const d = dur();
      if (!d) return;
      const [t0, t1] = win(), ppm = pxPerMs();
      // ruler: frame ticks once they are ≥6px apart, labelled steps ≥ ~56px apart
      g.fillStyle = line;
      g.fillRect(0, RULER - 1, viewW, 1);
      const frameMs = 1000 / fps;
      if (frameMs * ppm >= 6) {
        for (let f = Math.floor(t0 / frameMs); f * frameMs <= t1; f++) { const x = (f * frameMs - t0) * ppm; g.fillRect(Math.round(x), RULER - 5, 1, 4); }
      }
      const steps = [100, 200, 500, 1000, 2000, 5000, 10000, 15000, 30000, 60000];
      const frameLabels = frameMs * ppm >= 34;
      const step = frameLabels ? frameMs : (steps.find((s) => s * ppm >= 56) || 60000);
      g.font = '500 10.5px -apple-system, system-ui, sans-serif';
      g.textBaseline = 'top';
      for (let i = Math.floor(t0 / step); i * step <= t1; i++) {
        const t = i * step, x = (t - t0) * ppm;
        g.fillStyle = line; g.fillRect(Math.round(x), 0, 1, RULER - 1);
        g.fillStyle = text2;
        const lab = frameLabels ? `f${i}` : step < 1000 ? `${(t / 1000).toFixed(1)}s` : t >= 60000 ? `${Math.floor(t / 60000)}:${String(Math.floor(t / 1000) % 60).padStart(2, '0')}` : `${Math.round(t / 1000)}s`;
        g.fillText(lab, Math.round(x) + 4, 5);
      }
      // waveform from the finest peaks that still cover a bar each
      const pk = D.peaks, mid = RULER + WAVE / 2;
      g.fillStyle = ink;
      if (!pk) { g.fillRect(0, mid, viewW, 1); return; }
      const bw = 2, gap = 1, msPerBar = (bw + gap) / ppm;
      const coarsePer = pk.coarse.length / pk.durationMs;
      const useFine = pk.fine && coarsePer * msPerBar < 2;
      const arr = useFine ? pk.fine : pk.coarse, per = useFine ? pk.finePerSec / 1000 : coarsePer;
      const startX = Math.max(0, -t0 * ppm), endX = Math.min(viewW, (pk.durationMs - t0) * ppm);
      for (let x = startX - (startX % (bw + gap)); x < endX; x += bw + gap) {
        const a = Math.floor((t0 + x / ppm) * per), b = Math.max(a + 1, Math.floor((t0 + (x + bw + gap) / ppm) * per));
        let m = 0; for (let k = Math.max(0, a), e = Math.min(arr.length, b); k < e; k++) if (arr[k] > m) m = arr[k];
        const h = Math.max(1, m * (WAVE - 10));
        g.fillRect(x, mid - h / 2, bw, h);
      }
      drawOverview(ink);
    }
    function drawOverview(ink) {
      el.classList.toggle('is-zoomed', z > 1.01);
      const r = ov.getBoundingClientRect(), dpr = devicePixelRatio || 1;
      if (!r.width) return;
      if (ov.width !== Math.round(r.width * dpr)) { ov.width = Math.round(r.width * dpr); ov.height = Math.round(r.height * dpr); }
      const g = ov.getContext('2d');
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
      g.clearRect(0, 0, r.width, r.height);
      const pk = D.peaks, d = dur();
      if (pk && d) {
        g.globalAlpha = 0.55; g.fillStyle = ink;
        const n = pk.coarse.length, span = pk.durationMs / d;
        for (let x = 0; x < r.width * span; x += 2) {
          const i = Math.floor((x / (r.width * span)) * n), e = Math.min(n, Math.floor(((x + 2) / (r.width * span)) * n) + 1);
          let m = 0; for (let k = i; k < e; k++) if (pk.coarse[k] > m) m = pk.coarse[k];
          const h = Math.max(1, m * (r.height - 4)); g.fillRect(x, (r.height - h) / 2, 1.5, h);
        }
        g.globalAlpha = 1;
      }
      const [t0, t1] = win();
      ovWin.style.left = `${(t0 / (d || 1)) * 100}%`;
      ovWin.style.width = `${((t1 - t0) / (d || 1)) * 100}%`;
    }

    // ── markers, pins, legend ──
    function paintMarks() {
      const d = dur();
      if (!d) { $('.hsc-mks').innerHTML = ''; $('.hsc-pins').innerHTML = ''; return; }
      const pct = (ms) => `${clamp((ms / d) * 100, 0, 100).toFixed(4)}%`;
      $('.hsc-mks').innerHTML = D.markers.map((m, i) => `<i class="hsc-mk k-${m.kind}" data-mi="${i}" style="left:${pct(m.ms)}">${m.short ? `<b>${esc(m.short)}</b>` : ''}</i>`).join('');
      $('.hsc-pins').innerHTML = D.pins.map((p) => `<button type="button" class="hsc-pin${p.on ? ' is-on' : ''}" data-kp="${esc(p.id)}" style="left:${pct(p.ms)}" title="${esc(p.label || '')}"><span>${p.n}</span></button>`).join('');
      const counts = {};
      D.markers.forEach((m) => { counts[m.kind] = (counts[m.kind] || 0) + 1; });
      const legend = ORDER.filter((k) => counts[k]).map((k) => `<span class="hsc-lg"><i class="k-${k}"></i><b>${KINDS[k][0]} ${counts[k]}</b><em> · ${KINDS[k][1]}</em></span>`);
      if (D.pins.length || D.markMode) legend.push(`<span class="hsc-lg"><i class="k-pin"></i><b>Your moments ${D.pins.length}</b><em> · keypoints you marked</em></span>`);
      $('.hsc-legend').innerHTML = legend.join('');
      $('.hsc-legend').hidden = !legend.length;
      zoomClass();
    }
    function zoomClass() {
      const [t0, t1] = win(), span = t1 - t0;
      el.classList.toggle('z-lab', span > 0 && span <= 8000);
      el.classList.toggle('z-lab2', span > 0 && span <= 3000);
    }

    // ── playhead ──
    function paintPh() {
      const d = dur(), t = now();
      if (!d) return;
      const p = clamp(t / d, 0, 1);
      ph.style.left = `${(p * 100).toFixed(4)}%`;
      ovPh.style.left = `${(p * 100).toFixed(3)}%`;
      timeEl.textContent = `${fmtT(t)} / ${fmtT(d)}`;
    }
    function follow() {
      if (z <= 1.01) return;
      const x = (now() / dur()) * contentW(), l = scroll.scrollLeft;
      if (x < l || x > l + viewW * 0.85) scroll.scrollLeft = x - viewW * 0.15;
    }
    // playback window: the trim selection (always), else the loop range when looping
    const bounds = () => (D.trim ? [D.trim.startMs, D.trim.endMs] : loop && range ? range : null);
    function tick() {
      const b = bounds();
      if (b && audio.currentTime * 1000 >= b[1]) {
        if (loop) audio.currentTime = b[0] / 1000;
        else { audio.pause(); pendingSeek = null; }
      }
      paintPh();
      follow();
      if (!audio.paused) raf = requestAnimationFrame(tick);
    }
    audio.addEventListener('play', () => { pendingSeek = null; playB.innerHTML = icon('PauseIcon', 22); playB.setAttribute('aria-label', 'Pause'); cancelAnimationFrame(raf); raf = requestAnimationFrame(tick); });
    audio.addEventListener('pause', () => { playB.innerHTML = icon('PlayIcon', 22); playB.setAttribute('aria-label', 'Play'); cancelAnimationFrame(raf); paintPh(); });
    audio.addEventListener('ended', () => { const b = bounds(); if (loop && b) { audio.currentTime = b[0] / 1000; audio.play().catch(() => {}); } });
    audio.addEventListener('loadedmetadata', () => { if (opts.onMeta && audio.duration) opts.onMeta(audio.duration * 1000); if (pendingSeek != null) { try { audio.currentTime = pendingSeek / 1000; } catch (_) {} } if (!D.durationMs) { layout(); paintMarks(); } paintPh(); });
    audio.addEventListener('seeked', () => { pendingSeek = null; paintPh(); });

    function seek(ms) {
      ms = clamp(ms, 0, dur());
      if (audio.readyState >= 1) { try { audio.currentTime = ms / 1000; } catch (_) {} pendingSeek = audio.paused ? ms : null; }
      else pendingSeek = ms;
      paintPh();
    }
    function toggle() {
      if (!audio.src) return;
      if (audio.paused) {
        const b = bounds();
        if (b && (now() < b[0] || now() >= b[1] - 15)) seek(b[0]);
        if (pendingSeek != null && audio.readyState >= 1) { try { audio.currentTime = pendingSeek / 1000; } catch (_) {} }
        audio.play().catch(() => {});
      } else audio.pause();
    }
    const pause = () => { try { audio.pause(); } catch (_) {} };

    // ── zoom ──
    function setZoom(nz, anchorX) {
      nz = clamp(nz, 1, zMax());
      if (!viewW || Math.abs(nz - z) < 1e-4) return;
      const ax = anchorX == null ? clamp((now() / dur()) * contentW() - scroll.scrollLeft, 0, viewW) : anchorX;
      const t = ((scroll.scrollLeft + ax) / contentW()) * dur();
      z = nz;
      content.style.width = `${Math.round(contentW())}px`;
      scroll.scrollLeft = (t / dur()) * contentW() - ax;
      slider.value = String(Math.round((Math.log(z) / Math.log(zMax() || 1.0001)) * 1000) || 0);
      zoomClass();
      draw();
    }
    slider.addEventListener('input', () => setZoom(Math.pow(zMax(), +slider.value / 1000)));
    el.querySelectorAll('.hsc-zb').forEach((b) => b.addEventListener('click', () => setZoom(z * (b.dataset.z === '1' ? 2 : 0.5))));
    let keepLeft = 0; // the studio re-renders around this element; re-attaching resets scrollLeft, so keep it
    scroll.addEventListener('scroll', () => { if (scroll.isConnected) keepLeft = scroll.scrollLeft; schedule(); zoomClass(); }, { passive: true });
    scroll.addEventListener('wheel', (e) => {
      if (!(e.ctrlKey || e.metaKey)) return; // plain wheel: page scroll / horizontal pan stay native
      e.preventDefault();
      const r = scroll.getBoundingClientRect();
      setZoom(z * Math.exp(-e.deltaY * 0.01), e.clientX - r.left);
    }, { passive: false });
    // pinch: Safari (macOS trackpad + iOS) sends gesture events; others get two-finger touch handling
    let g0 = null;
    if ('ongesturechange' in window) {
      scroll.addEventListener('gesturestart', (e) => { e.preventDefault(); g0 = { z, x: e.clientX - scroll.getBoundingClientRect().left }; });
      scroll.addEventListener('gesturechange', (e) => { e.preventDefault(); if (g0) setZoom(g0.z * e.scale, g0.x); });
      scroll.addEventListener('gestureend', (e) => { e.preventDefault(); g0 = null; });
    } else {
      let p0 = null;
      const dist = (t) => Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY);
      scroll.addEventListener('touchstart', (e) => { if (e.touches.length === 2) p0 = { d: dist(e.touches), z, x: (e.touches[0].clientX + e.touches[1].clientX) / 2 - scroll.getBoundingClientRect().left }; }, { passive: true });
      scroll.addEventListener('touchmove', (e) => { if (p0 && e.touches.length === 2) { e.preventDefault(); setZoom(p0.z * (dist(e.touches) / p0.d), p0.x); } }, { passive: false });
      scroll.addEventListener('touchend', (e) => { if (e.touches.length < 2) p0 = null; }, { passive: true });
    }

    // ── overview strip: tap or drag to move the window ──
    const ovw = $('.hsc-ovw');
    let ovDrag = false;
    const ovTo = (e) => { const r = ovw.getBoundingClientRect(); const t = clamp((e.clientX - r.left) / r.width, 0, 1) * dur(); scroll.scrollLeft = (t / dur()) * contentW() - viewW / 2; };
    ovw.addEventListener('pointerdown', (e) => { ovDrag = true; ovw.setPointerCapture(e.pointerId); ovTo(e); });
    ovw.addEventListener('pointermove', (e) => { if (ovDrag) ovTo(e); });
    ovw.addEventListener('pointerup', () => { ovDrag = false; });
    ovw.addEventListener('pointercancel', () => { ovDrag = false; });

    // ── pointer: tap = seek, double-tap = mark, ruler/playhead drag = scrub, mouse drag on wave = range ──
    let pd = null;
    const xToMs = (clientX) => { const r = content.getBoundingClientRect(); return clamp(((clientX - r.left) / r.width) * dur(), 0, dur()); };
    scroll.addEventListener('pointerdown', (e) => {
      if (e.button > 0 || !dur()) return;
      if (e.target.closest('.hsc-pin')) return;
      const th = D.trim && e.target.closest('.hsc-th'), tw = D.trim && e.target.closest('.hsc-tw');
      if (th || tw) {
        pd = { id: e.pointerId, x0: e.clientX, y0: e.clientY, t: Date.now(), type: e.pointerType, mode: th ? (th.classList.contains('l') ? 'edgeL' : 'edgeR') : 'move',
          s0: D.trim.startMs, e0: D.trim.endMs, ms0: xToMs(e.clientX), moved: false };
        scroll.setPointerCapture(e.pointerId); e.preventDefault();
        el.classList.add('is-trimming');
        if (th) th.classList.add('is-active');
        pd.lastX = e.clientX; startPan();
        return;
      }
      const scrub = !!e.target.closest('.hsc-ph-h, .hsc-rul');
      pd = { id: e.pointerId, x0: e.clientX, y0: e.clientY, t: Date.now(), type: e.pointerType, mode: scrub ? 'scrub' : 'tap', mk: e.target.closest('.hsc-mk'), wasPlaying: !audio.paused };
      if (scrub) { scroll.setPointerCapture(e.pointerId); e.preventDefault(); seek(xToMs(e.clientX)); pd.lastX = e.clientX; startPan(); }
    });
    scroll.addEventListener('pointermove', (e) => {
      if (!pd || e.pointerId !== pd.id) { if (e.pointerType === 'mouse') hoverTip(e); return; }
      const dx = e.clientX - pd.x0;
      pd.lastX = e.clientX; pd.shift = e.shiftKey;
      if (pd.mode === 'scrub') { seek(xToMs(e.clientX)); return; }
      if (pd.mode === 'move' || pd.mode === 'edgeL' || pd.mode === 'edgeR') {
        if (!pd.moved && Math.abs(dx) < 4) return;
        pd.moved = true;
        trimDrag(pd, xToMs(e.clientX), e.shiftKey);
        return;
      }
      if (pd.mode === 'tap' && pd.type === 'mouse' && Math.abs(dx) > 4) { pd.mode = 'range'; scroll.setPointerCapture(e.pointerId); pd.ms0 = xToMs(pd.x0); startPan(); }
      if (pd.mode === 'range') { const a = xToMs(e.clientX); range = [Math.min(a, pd.ms0), Math.max(a, pd.ms0)]; paintRange(); }
    });
    scroll.addEventListener('pointercancel', () => { el.querySelectorAll('.hsc-th.is-active').forEach((h) => h.classList.remove('is-active')); if (pd && pd.moved && opts.onTrim && D.trim) opts.onTrim({ ...D.trim }, pd.mode === 'move' ? 'move' : 'edge', true); pd = null; el.classList.remove('is-trimming'); }); // the browser took the gesture (pan/pinch)
    scroll.addEventListener('pointerup', (e) => {
      if (!pd || e.pointerId !== pd.id) return;
      const p = pd; pd = null;
      el.querySelectorAll('.hsc-th.is-active').forEach((h) => h.classList.remove('is-active'));
      if (p.mode === 'scrub') return;
      if (p.mode === 'move' || p.mode === 'edgeL' || p.mode === 'edgeR') {
        el.classList.remove('is-trimming');
        if (p.moved) { if (opts.onTrim) opts.onTrim({ ...D.trim }, p.mode === 'move' ? 'move' : 'edge', true); return; }
        seek(clamp(xToMs(e.clientX), D.trim.startMs, D.trim.endMs)); // a tap inside the section seeks
        return;
      }
      if (p.mode === 'range') { if (range && range[1] - range[0] < 40) { range = null; paintRange(); } else setLoop(true); return; }
      if (Math.abs(e.clientX - p.x0) > 8 || Math.abs(e.clientY - p.y0) > 8 || Date.now() - p.t > 600) return; // a pan, not a tap
      const ms = xToMs(e.clientX);
      if (p.mk) { const m = D.markers[+p.mk.dataset.mi]; if (m) { seek(m.ms); showTip(m, p.mk, true); } }
      else seek(ms);
      if (D.markMode && lastTap && Date.now() - lastTap.t < 350 && Math.abs(e.clientX - lastTap.x) < 18) { lastTap = null; if (opts.onMark) opts.onMark(ms); return; }
      lastTap = { t: Date.now(), x: e.clientX };
    });
    el.addEventListener('click', (e) => {
      const pin = e.target.closest('.hsc-pin');
      if (pin) { const p = D.pins.find((x) => x.id === pin.dataset.kp); if (p) seek(p.ms); if (opts.onPin) opts.onPin(pin.dataset.kp); return; }
      if (e.target.closest('.hsc-play')) return toggle();
      if (e.target.closest('.hsc-loop')) return setLoop(!loop);
      if (e.target.closest('.hsc-markb')) { if (opts.onMark) opts.onMark(now()); }
    });

    // ── edge auto-pan: while dragging (scrub, range, trim window or handle) near or past the edge of the
    // visible area, the view scrolls with the pointer, faster the further past the edge it is ──
    let panRaf = 0;
    function startPan() {
      if (panRaf) return;
      const step = () => {
        panRaf = 0;
        if (!pd || !/^(scrub|range|move|edgeL|edgeR)$/.test(pd.mode) || pd.lastX == null) return;
        const r = scroll.getBoundingClientRect(), EDGE = Math.min(56, r.width * 0.12);
        const over = pd.lastX < r.left + EDGE ? pd.lastX - (r.left + EDGE) : pd.lastX > r.right - EDGE ? pd.lastX - (r.right - EDGE) : 0;
        if (over && z > 1.01) {
          const v = Math.sign(over) * Math.min(48, 2 + Math.pow(Math.abs(over) / 6, 1.6));
          const before = scroll.scrollLeft;
          scroll.scrollLeft = clamp(before + v, 0, contentW() - viewW);
          if (scroll.scrollLeft !== before) {
            const ms = xToMs(clamp(pd.lastX, r.left, r.right));
            if (pd.mode === 'scrub') seek(ms);
            else if (pd.mode === 'range') { range = [Math.min(ms, pd.ms0), Math.max(ms, pd.ms0)]; paintRange(); }
            else if (pd.moved) trimDrag(pd, ms, pd.shift);
          }
        }
        panRaf = requestAnimationFrame(step);
      };
      panRaf = requestAnimationFrame(step);
    }

    // ── trim window ──
    function snapTo(ms, shift) {
      const on = !!(opts.snap && opts.snap()) !== !!shift; // Shift inverts the toggle
      if (!on || !D.snapPts.length) return ms;
      const lim = 12 / (pxPerMs() || 1);
      let best = ms, bd = lim;
      for (const p of D.snapPts) { const d = Math.abs(p - ms); if (d < bd) { bd = d; best = p; } }
      return best;
    }
    function trimDrag(p, ms, shift) {
      const d = dur();
      let s = p.s0, e = p.e0;
      if (p.mode === 'move') {
        const len = p.e0 - p.s0;
        s = clamp(p.s0 + (ms - p.ms0), 0, d - len);
        const ss = snapTo(s, shift), es = snapTo(s + len, shift);
        if (ss !== s && Math.abs(ss - s) <= Math.abs(es - (s + len))) s = ss;
        else if (es !== s + len) s = es - len;
        s = clamp(s, 0, d - len); e = s + len;
      } else if (p.mode === 'edgeL') s = clamp(snapTo(ms, shift), 0, p.e0 - MIN_TRIM);
      else e = clamp(snapTo(ms, shift), p.s0 + MIN_TRIM, d);
      D.trim = { startMs: Math.round(s), endMs: Math.round(e) }; // whole ms; the cut is sample-accurate from these
      paintTrim();
      if (opts.onTrim) opts.onTrim({ ...D.trim }, p.mode === 'move' ? 'move' : 'edge', false);
    }
    function paintTrim() {
      const box = $('.hsc-trim'), d = dur();
      el.classList.toggle('has-trim', !!D.trim);
      if (!D.trim || !d) { box.hidden = true; return; }
      box.hidden = false;
      const a = (D.trim.startMs / d) * 100, b = (D.trim.endMs / d) * 100;
      $('.hsc-dim.l').style.width = `${a}%`;
      $('.hsc-dim.r').style.left = `${b}%`;
      const tw = $('.hsc-tw'); tw.style.left = `${a}%`; tw.style.width = `${b - a}%`;
    }

    // ── loop range ──
    function paintRange() {
      if (opts.onRange) opts.onRange(range && !D.trim ? [range[0], range[1]] : null);
      const r = $('.hsc-loopr'), d = dur();
      if (!range || !d || D.trim) { r.hidden = true; return; }
      r.hidden = false;
      r.style.left = `${(range[0] / d) * 100}%`;
      r.style.width = `${((range[1] - range[0]) / d) * 100}%`;
      r.classList.toggle('is-on', loop);
    }
    function setLoop(on) {
      loop = on;
      if (on && !range && !D.trim) { const t = now(); range = [Math.max(0, t - 1000), Math.min(dur(), t + 1000)]; }
      if (!on) range = null;
      $('.hsc-loop').setAttribute('aria-pressed', String(loop));
      $('.hsc-loop').classList.toggle('is-on', loop);
      paintRange();
    }

    // ── tooltips ──
    function showTip(m, node, sticky) {
      const vr = $('.hsc-view').getBoundingClientRect(), r = node.getBoundingClientRect();
      tip.innerHTML = `<b>${esc(KINDS[m.kind] ? KINDS[m.kind][0].replace(/s$/, '') : m.kind)} · ${fmtT(m.ms)}</b><span>${esc(m.detail || m.label || '')}</span><em>${esc(m.id)}</em>`;
      tip.hidden = false;
      const w = tip.offsetWidth;
      tip.style.left = `${clamp(r.left - vr.left - w / 2, 0, Math.max(0, vr.width - w))}px`;
      clearTimeout(tipT); tipT = 0;
      if (sticky) tipT = setTimeout(() => { tip.hidden = true; tipT = 0; }, 2800);
    }
    function hoverTip(e) {
      const mk = e.target.closest && e.target.closest('.hsc-mk');
      if (mk) { const m = D.markers[+mk.dataset.mi]; if (m) showTip(m, mk, false); }
      else if (!tipT) tip.hidden = true;
    }
    scroll.addEventListener('pointerleave', (e) => { if (e.pointerType === 'mouse' && !tipT) tip.hidden = true; });

    // ── keys (the studio forwards keydown when focus is not in a text field) ──
    function key(e) {
      if (!dur()) return false;
      if (e.key === ' ' || e.code === 'Space') { toggle(); return true; }
      if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
        const dir = e.key === 'ArrowRight' ? 1 : -1, t = now();
        if (e.shiftKey) {
          const pts = D.markers.map((m) => m.ms).concat(D.pins.map((p) => p.ms)).sort((a, b) => a - b);
          const nx = dir > 0 ? pts.find((x) => x > t + 1) : pts.slice().reverse().find((x) => x < t - 1);
          if (nx != null) seek(nx);
        } else {
          const f = Math.floor((t * fps) / 1000 + 0.5) + dir;
          seek((f * 1000) / fps);
        }
        follow();
        return true;
      }
      if ((e.key === 'm' || e.key === 'M') && D.markMode && !e.metaKey && !e.ctrlKey) { if (opts.onMark) opts.onMark(now()); return true; }
      if (e.key === '=' || e.key === '+') { setZoom(z * 2); return true; }
      if (e.key === '-' || e.key === '_') { setZoom(z / 2); return true; }
      return false;
    }

    // ── data ──
    function set(next) {
      const prevSrc = D.src, prevDur = dur();
      D = { ...D, ...next };
      if (D.src !== prevSrc) {
        const t = now(); pause();
        if (D.src) { audio.src = D.src; try { audio.load(); } catch (_) {} pendingSeek = t || null; } else audio.removeAttribute('src');
      }
      $('.hsc-markb').hidden = !D.markMode;
      $('.hsc-hint').textContent = D.trim ? 'Drag the section to move it · drag an edge to resize' : D.markMode ? 'Tap to seek · double-tap or Mark here (M) adds a moment' : 'Tap to seek · drag the ruler to scrub';
      $('.hsc-loop').title = D.trim ? 'Loop the section' : 'Loop the selected range (or 2 s around the playhead)';
      if (!pd || !/^(move|edge)/.test(pd.mode)) paintTrim();
      if (dur() !== prevDur) { z = clamp(z, 1, zMax()); layout(); }
      const mkKey = [D.markers.length, D.markers[0] && D.markers[0].id, dur(), D.markMode, JSON.stringify(D.pins)].join('|');
      if (mkKey !== lastMk || D.markers !== lastMarkers) { lastMk = mkKey; lastMarkers = D.markers; paintMarks(); }
      paintRange();
      schedule();
      paintPh();
    }
    function attach(slot) {
      if (!slot) return;
      if (el.parentNode !== slot) { slot.appendChild(el); if (viewW) content.style.width = `${Math.round(contentW())}px`; scroll.scrollLeft = keepLeft; }
      requestAnimationFrame(() => { layout(); paintTrim(); });
    }
    const ro = 'ResizeObserver' in window ? new ResizeObserver(() => { if (scroll.clientWidth && scroll.clientWidth !== viewW) layout(); }) : null;
    if (ro) ro.observe(scroll); else addEventListener('resize', layout);
    function destroy() { pause(); cancelAnimationFrame(raf); if (ro) ro.disconnect(); el.remove(); audio.removeAttribute('src'); }

    return { el, set, attach, key, toggle, pause, seek, time: now, pxPerMs, destroy, playing: () => !audio.paused, reducedMotion: reduced };
  }

  window.hxScrub = { create, fmt: fmtT };
})();
