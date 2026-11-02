/* Hermes — message queue you can see and push from (plain JS, no build step).
 *
 * Upstream already keeps a per-session client queue (ui.js SESSION_QUEUES + the #queueCard flyout)
 * and drains it one message per finished turn. But the default "busy" mode is steer, so a message
 * typed while a turn runs is injected straight into it and never shows up in a queue. This:
 *   1. Makes busy sends QUEUE when the user's mode is the default 'steer' (an explicit
 *      'queue' / 'interrupt' choice in Settings is left alone). Done by shadowing
 *      window._defaultMessageMode, the value upstream reads on the send path; the saved
 *      setting itself is untouched. /steer, /interrupt etc. still work as typed.
 *   2. Restyles upstream's queue card (header always shown, minimal rows) and adds a
 *      "Send now" action on each queued row: POST /api/chat/steer — the same endpoint
 *      upstream's steer uses — which pushes the text into the running turn. Edit-in-place,
 *      remove, reorder, combine and clear stay upstream's.
 *   3. ⌘/Ctrl+Enter in the composer while a turn runs = queue it and push it straight in.
 *      A small "Send now" button beside the send button does the same.
 *   4. While a Claude Code worker runs, queued messages go straight to it (it reads them at its next
 *      step) and Send now interrupts its current step first. Without a live worker (Codex, or the
 *      hub replying by itself) both fall back to the steer above.
 * Never drops a message: if the push is refused the item stays queued (it drains when the turn
 * ends, as upstream does), and if the turn already ended it is sent as a normal message. A toast
 * says which happened.
 */
(function () {
  'use strict';
  if (window.__hxMessageQueue) return;
  window.__hxMessageQueue = true;

  const icon = (n, s) => (typeof window.hxIcon === 'function' ? window.hxIcon(n, s) : '');
  const has = (name) => { try { return typeof window[name] === 'function'; } catch (_) { return false; } };
  const st = () => (typeof S !== 'undefined' ? S : null);
  const curSid = () => { const s = st(); return (s && s.session && s.session.session_id) || null; };
  const toast = (m, ms, type) => { try { showToast(m, ms || 3200, type); } catch (_) {} };
  const isMac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || '');
  const CHORD = isMac ? '⌘↵' : 'Ctrl+↵';
  const pushing = new Set(); // _queued_at stamps currently being pushed

  // ── 1. queue instead of silent steer ───────────────────────────────────────
  let userMode = window._defaultMessageMode;
  try {
    Object.defineProperty(window, '_defaultMessageMode', {
      configurable: true,
      get() { return (userMode === 'queue' || userMode === 'interrupt') ? userMode : 'queue'; },
      set(v) { userMode = v; },
    });
  } catch (_) {}

  // ── queue helpers (upstream globals, reached by name) ──────────────────────
  const liveQueue = (sid) => { try { return _getSessionQueue(sid, false); } catch (_) { return []; } };
  function saveQueue(sid, q) {
    try {
      if (!q.length) { delete SESSION_QUEUES[sid]; _clearPersistedSessionQueue(sid); }
      else { SESSION_QUEUES[sid] = q; _persistSessionQueueStorage(sid, q); }
      delete _queueRenderKeys[sid];
    } catch (_) {}
    try { updateQueueBadge(sid); } catch (_) {}
  }
  function takeEntry(sid, ts) {
    const q = liveQueue(sid);
    const i = q.findIndex((e) => e && e._queued_at === ts);
    if (i === -1) return null;
    const entry = q.splice(i, 1)[0];
    saveQueue(sid, [...q]);
    return { entry, index: i };
  }
  function putBack(sid, entry, index) {
    try {
      const q = _getSessionQueue(sid, true);
      q.splice(Math.min(index, q.length), 0, entry);
      saveQueue(sid, [...q]);
    } catch (_) {}
  }
  const entryText = (e) => String((e && (e.text || e.message || e.content)) || '').trim();
  const realFiles = (e) => (e && Array.isArray(e.files) ? e.files.filter((f) => typeof Blob !== 'undefined' && f instanceof Blob) : []);
  const runningFor = (sid) => { const s = st(); return !!(s && s.busy && s.activeStreamId && curSid() === sid); };

  // Send a (removed) entry as a normal turn, the way upstream's drain does.
  function sendAsTurn(entry) {
    const s = st(), msg = document.getElementById('msg');
    if (!s || !msg || !has('send') || msg.value.trim() || (s.pendingFiles && s.pendingFiles.length)) return false;
    msg.value = entryText(entry);
    s.pendingFiles = realFiles(entry);
    try { autoResize(); renderTray(); } catch (_) {}
    send({ goalContinuationId: entry.goal_continuation_id || '' });
    return true;
  }

  // ── 2a. straight to the running Claude Code worker ────────────────────────
  // While the hub waits on a Claude Code worker, steering the hub only lands when the worker is done.
  // The helper writes the message into the worker's own input instead (providers.LiveInput):
  // "queue" = Claude reads it at its next step; "now" = it stops the current step and reads it.
  const SIDE = 'api/extensions/cc-agents/sidecar/worker';
  async function sideJSON(url, body) {
    const r = await fetch(url, body ? { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
      : { credentials: 'same-origin', cache: 'no-store' });
    if (!r.ok) throw new Error(String(r.status));
    return r.json();
  }
  let liveCache = { sid: null, at: 0, open: false };
  async function workerOpen(sid) {
    if (liveCache.sid === sid && Date.now() - liveCache.at < 4000) return liveCache.open;
    let open = false;
    try { open = !!(await sideJSON(`${SIDE}?session=${encodeURIComponent(sid)}`)).open; } catch (_) {}
    liveCache = { sid, at: Date.now(), open };
    return open;
  }
  // true = Claude has it; false = no live worker (or it closed before reading it): use the old path
  async function toWorker(sid, text, mode) {
    let res;
    try { res = await sideJSON(`${SIDE}/message`, { session: sid, text, mode }); } catch (_) { return false; }
    if (!res || !res.ok) return false;
    for (let i = 0; i < 12; i++) {
      await new Promise((r) => setTimeout(r, 700));
      try { if ((await sideJSON(`${SIDE}/ack?request=${encodeURIComponent(res.request_id)}&id=${res.id}`)).delivered) return true; } catch (_) {}
    }
    return false;
  }

  // ── 2. push one queued entry straight into the running turn ───────────────
  async function pushNow(sid, ts, mode) {
    if (!sid || ts == null || pushing.has(ts)) return;
    const taken = takeEntry(sid, ts); // out of the queue first so a drain can't also send it
    if (!taken) return;
    const { entry, index } = taken;
    const text0 = entryText(entry);
    if (text0 && !realFiles(entry).length && runningFor(sid) && await workerOpen(sid)) {
      pushing.add(ts);
      const ok = await toWorker(sid, text0, mode === 'queue' ? 'queue' : 'now');
      pushing.delete(ts);
      if (ok) {
        if (curSid() === sid && has('_showSteerIndicator')) _showSteerIndicator(text0);
        toast(mode === 'queue' ? 'Passed to Claude: it reads it at its next step' : 'Sent to Claude: it paused and is reading it now', 2600);
        return;
      }
      liveCache.at = 0;
    }

    if (!runningFor(sid)) {
      // Nothing running to push into: just send it now as a normal message.
      if (curSid() === sid && !(st() && st().busy) && sendAsTurn(entry)) return;
      putBack(sid, entry, index);
      toast('Nothing running to push into, so it stays queued', 3200);
      return;
    }

    pushing.add(ts);
    let res = null;
    try {
      let text = text0;
      const files = realFiles(entry);
      if (files.length && has('_steerTextWithPendingFiles')) text = await _steerTextWithPendingFiles(text0, sid, files);
      if (!text) throw Object.assign(new Error('empty'), { status: 400 });
      res = await api('/api/chat/steer', { method: 'POST', retries: 0, body: JSON.stringify({ session_id: sid, text }) });
      if (res && res.accepted) {
        try { const acc = _steerAcceptedBySid.get(sid) || []; acc.push(text); _steerAcceptedBySid.set(sid, acc); } catch (_) {}
        if (curSid() === sid && has('_showSteerIndicator')) _showSteerIndicator(text0 || text);
        toast('Sent into the current reply', 2200);
        return;
      }
    } catch (e) {
      res = { accepted: false, fallback: 'network_error', uncertain: !(e && e.status) };
    } finally {
      pushing.delete(ts);
    }

    // Not delivered (or unknown): never drop it.
    const fb = res && res.fallback;
    if (fb === 'gateway_steer_uncertain' || (res && res.uncertain)) {
      putBack(sid, entry, index);
      toast('Couldn’t confirm it reached the agent, so it stays queued. Remove it if the reply picks it up.', 5000, 'warning');
      return;
    }
    if (!runningFor(sid) && curSid() === sid && !(st() && st().busy) && sendAsTurn(entry)) {
      toast('The reply had finished, so it was sent as a new message', 3200);
      return;
    }
    putBack(sid, entry, index);
    let why = '';
    try { why = t(_steerFailureMessageKey(fb)); } catch (_) {}
    toast((why ? why + '. ' : 'Couldn’t push it in. ') + 'It stays queued and sends after this reply.', 4500, 'warning');
  }
  window.hxQueuePushNow = pushNow;

  // Queued messages don't wait for the whole reply: while a Claude Code worker runs, each one is
  // handed to it (like typing ahead in Claude Code), oldest first. Ones with files keep waiting.
  let forwarding = false;
  setInterval(async () => {
    const sid = curSid();
    if (forwarding || !sid || !runningFor(sid) || document.hidden) return;
    const next = liveQueue(sid).find((e) => e && entryText(e) && !realFiles(e).length && !pushing.has(e._queued_at));
    if (!next || !(await workerOpen(sid))) return;
    forwarding = true;
    try { await pushNow(sid, next._queued_at, 'queue'); } finally { forwarding = false; }
  }, 2500);

  // ── restyle/augment upstream's card after each render ─────────────────────
  function decorate(sid) {
    const inner = document.getElementById('queueChips');
    if (!inner || !sid) return;
    const q = liveQueue(sid);
    const card = document.getElementById('queueCard');
    if (card) card.classList.add('hxq');
    if (!q.length) return;
    const running = runningFor(sid);

    let header = inner.querySelector('.queue-card-header');
    if (!header) {
      // Upstream only shows a header for 2+ items; give the single item one too.
      header = document.createElement('div');
      header.className = 'queue-card-header hxq-single';
      const lbl = document.createElement('span');
      lbl.textContent = '1 queued';
      const actions = document.createElement('span');
      actions.className = 'queue-card-header-actions';
      const hide = document.createElement('button');
      hide.className = 'queue-card-icon-btn';
      hide.type = 'button';
      hide.title = 'Hide queue';
      hide.setAttribute('aria-label', 'Hide queue panel');
      hide.innerHTML = icon('ArrowDown01Icon', 14);
      hide.onclick = () => {
        try { _queueCollapsed[sid] = true; } catch (_) {}
        if (card) card.classList.remove('visible');
        try { _updateQueuePill(sid, liveQueue(sid).length); } catch (_) {}
      };
      actions.appendChild(hide);
      header.appendChild(lbl);
      header.appendChild(actions);
      inner.insertBefore(header, inner.firstChild);
    }
    let hint = header.querySelector('.hxq-hint');
    if (!hint) {
      hint = document.createElement('span');
      hint.className = 'hxq-hint';
      header.firstChild.after(hint);
    }
    hint.textContent = running ? 'sends after this reply' : 'sends next';

    const rows = inner.querySelectorAll('.queue-card-row');
    rows.forEach((row, i) => {
      const entry = q[i];
      const ts = entry && entry._queued_at;
      let btn = row.querySelector('.hxq-now');
      if (!btn) {
        btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'hxq-now';
        btn.setAttribute('draggable', 'false');
        btn.innerHTML = icon('ArrowUp02Icon', 13) + '<span>Send now</span>';
        const del = row.querySelector('.queue-card-icon-btn');
        row.insertBefore(btn, del || null);
      }
      btn.dataset.ts = String(ts);
      btn.title = running ? 'Push into the running reply now' : 'Send this now';
      btn.setAttribute('aria-label', btn.title);
      btn.disabled = pushing.has(ts);
      btn.onclick = (ev) => { ev.stopPropagation(); btn.disabled = true; pushNow(sid, ts); };
    });
  }

  function wrap(name, after) {
    const orig = window[name];
    if (typeof orig !== 'function' || orig.__hxq) return false;
    const w = function () { const r = orig.apply(this, arguments); try { after.apply(this, arguments); } catch (_) {} return r; };
    w.__hxq = true;
    window[name] = w;
    return true;
  }

  // ── 3. composer: ⌘/Ctrl+Enter and a small "Send now" button while busy ────
  let nowBtn = null;
  function composerCanPush() {
    const s = st(), msg = document.getElementById('msg');
    if (!s || !s.busy || !s.activeStreamId || !msg || msg.disabled) return false;
    const v = msg.value.trim();
    if (!v && !(s.pendingFiles && s.pendingFiles.length)) return false;
    if (v.startsWith('/')) return false; // slash commands keep their own busy handling
    return window._defaultMessageMode === 'queue';
  }
  async function composerPushNow() {
    const s = st(), sid = curSid();
    if (!composerCanPush() || !sid) return false;
    const before = new Set(liveQueue(sid).map((e) => e && e._queued_at));
    await send(); // upstream queues it (mode is 'queue' while busy): files, model, draft handling
    const added = liveQueue(sid).filter((e) => e && !before.has(e._queued_at));
    const last = added[added.length - 1];
    if (last) await pushNow(sid, last._queued_at);
    return true;
  }
  function syncNowBtn() {
    const send = document.getElementById('btnSend');
    if (!send) return;
    if (!nowBtn || !nowBtn.isConnected) {
      nowBtn = document.createElement('button');
      nowBtn.type = 'button';
      nowBtn.className = 'hxq-composer-now';
      nowBtn.innerHTML = icon('ArrowUp02Icon', 14) + '<span>Send now</span>';
      nowBtn.title = `Push into the running reply (${CHORD})`;
      nowBtn.setAttribute('aria-label', 'Send now — push into the running reply');
      nowBtn.addEventListener('mousedown', (e) => e.preventDefault()); // keep composer focus
      nowBtn.addEventListener('click', () => { composerPushNow(); });
      send.parentElement.insertBefore(nowBtn, send);
    }
    nowBtn.classList.toggle('show', composerCanPush());
  }
  function busyPlaceholder() {
    const s = st(), msg = document.getElementById('msg');
    if (!s || !msg || !s.busy || !s.activeStreamId || msg.value) return;
    if (window._defaultMessageMode !== 'queue' || !window._showBusyPlaceholderHint) return;
    msg.placeholder = `Enter to queue · ${CHORD} to send now`;
  }

  function onKey(e) {
    if (e.key !== 'Enter' || e.isComposing || e.altKey) return;
    if (!e.target || e.target.id !== 'msg') return;
    const sendKey = window._sendKey || 'enter';
    // ⌘/Ctrl+Enter, or ⌘/Ctrl+Shift+Enter when ⌘/Ctrl+Enter is the user's send key.
    const mod = e.metaKey || e.ctrlKey;
    const chord = sendKey === 'ctrl+enter' ? (mod && e.shiftKey) : (mod && !e.shiftKey);
    if (!chord || !composerCanPush()) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    composerPushNow();
  }

  let tries = 0;
  (function init() {
    const ready = ['_renderQueueChips', 'updateSendBtn', 'send'].every(has);
    if (!ready) { if (++tries < 100) setTimeout(init, 100); return; }
    wrap('_renderQueueChips', (sid) => decorate(sid));
    wrap('updateSendBtn', () => { syncNowBtn(); busyPlaceholder(); });
    wrap('_applyBusyComposerPlaceholder', busyPlaceholder);
    document.addEventListener('keydown', onKey, true);
    const msg = document.getElementById('msg');
    if (msg) msg.addEventListener('input', syncNowBtn);
    syncNowBtn();
    const sid = curSid();
    if (sid) { try { delete _queueRenderKeys[sid]; updateQueueBadge(sid); } catch (_) {} }
  })();
})();
