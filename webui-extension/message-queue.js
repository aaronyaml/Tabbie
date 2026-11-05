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
 *      hub replying by itself) both fall back to the steer above. Each one Claude takes leaves a
 *      "Sent to Claude" note under that turn's message, kept through re-renders.
 *   5. Composer Enter fixes: no stray newline after Enter queues a message, and Enter on a
 *      slash command that is already typed in full sends it instead of only closing the menu.
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
  // Stamps already delivered (to Claude, into the reply, or sent as a turn). A saved copy of the queue
  // read before the delivery (upstream re-queues a rejected send, then reloads the chat) must never
  // bring one back, so the restore and the forwarder skip these and the card drops them.
  const delivered = new Set();
  const markDelivered = (ts) => { if (ts != null) delivered.add(ts); };

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
  let liveCache = { sid: null, at: 0, open: false, reason: '' };
  async function workerOpen(sid) {
    if (liveCache.sid === sid && Date.now() - liveCache.at < 4000) return liveCache.open;
    let open = false, reason = '';
    try { const d = await sideJSON(`${SIDE}?session=${encodeURIComponent(sid)}`); open = !!d.open; reason = d.reason || ''; } catch (_) { reason = 'helper'; }
    liveCache = { sid, at: Date.now(), open, reason };
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
  // The hint/toast wording: a live worker will take text-only queued messages within seconds.
  const workerTakes = (sid) => liveCache.sid === sid && liveCache.open;

  // ── 2b. "Sent to Claude" notes ─────────────────────────────────────────────
  // Upstream's steer chip ("STEER", appended to #msgInner) is wiped by the next re-render, so a
  // message Claude took vanished from the chat. Each note here is anchored after the user message
  // of the turn it joined and put back whenever the transcript is rebuilt (memory only).
  const notes = new Map(); // sid -> [{ el, idx, raw }]
  const userRows = (inner) => Array.from(inner.querySelectorAll('.msg-row[data-role="user"]'));
  function placeNotes() {
    const inner = document.getElementById('msgInner');
    if (!inner || !notes.size) return;
    const list = notes.get(curSid()) || [];
    const mine = new Set(list.map((n) => n.el));
    inner.querySelectorAll('.hxq-sent').forEach((n) => { if (!mine.has(n)) n.remove(); }); // HTML-cache copies, other chats
    const rows = userRows(inner);
    let prev = null, prevRow = null;
    for (const n of list) {
      const row = rows.find((r) => r.dataset.sessionMsgIdx === n.idx && r.dataset.rawText === n.raw)
        || rows.find((r) => n.idx != null && r.dataset.sessionMsgIdx === n.idx)
        || rows.filter((r) => r.dataset.rawText === n.raw).pop();
      if (!row) { n.el.remove(); continue; } // turn not rendered (windowed out, or edited away)
      const after = row === prevRow && prev ? prev : row;
      if (after.nextElementSibling !== n.el) after.after(n.el); // no-op once placed: no observer loop
      prev = n.el; prevRow = row;
    }
  }
  let observed = null;
  const observer = typeof MutationObserver === 'function' ? new MutationObserver(placeNotes) : null;
  function watchTranscript() {
    const inner = document.getElementById('msgInner');
    if (!observer || !inner || inner === observed) return;
    observer.disconnect();
    observer.observe(inner, { childList: true });
    observed = inner;
  }
  function noteSent(sid, text) {
    const inner = document.getElementById('msgInner');
    const last = inner && userRows(inner).pop();
    if (!last) return;
    const el = document.createElement('div');
    el.className = 'hxq-sent';
    el.innerHTML = '<span class="hxq-sent-label">Sent to Claude</span><span class="hxq-sent-body"></span>';
    el.lastChild.textContent = text.length > 300 ? text.slice(0, 297) + '…' : text;
    const list = notes.get(sid) || [];
    list.push({ el, idx: last.dataset.sessionMsgIdx, raw: last.dataset.rawText });
    notes.set(sid, list.slice(-20));
    watchTranscript();
    placeNotes();
    try { scrollIfPinned(); } catch (_) {}
  }

  // ── 2. push one queued entry straight into the running turn ───────────────
  async function pushNow(sid, ts, mode) {
    if (!sid || ts == null || pushing.has(ts)) return;
    const taken = takeEntry(sid, ts); // out of the queue first so a drain can't also send it
    if (!taken) return;
    const { entry, index } = taken;
    const text0 = entryText(entry);
    if (runningFor(sid)) liveCache.at = 0;  // fresh state (and the reason for the toast)
    if (text0 && !realFiles(entry).length && runningFor(sid) && await workerOpen(sid)) {
      pushing.add(ts);
      const ok = await toWorker(sid, text0, mode === 'queue' ? 'queue' : 'now');
      pushing.delete(ts);
      if (ok) {
        markDelivered(ts);
        if (curSid() === sid) noteSent(sid, text0);
        toast(mode === 'queue' ? 'Passed to Claude: it reads it at its next step' : 'Sent to Claude: it paused and is reading it now', 2600);
        return;
      }
      liveCache.at = 0;
    }

    if (!runningFor(sid)) {
      // Nothing running to push into: just send it now as a normal message.
      if (curSid() === sid && !(st() && st().busy) && sendAsTurn(entry)) { markDelivered(ts); return; }
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
        markDelivered(ts);
        try { const acc = _steerAcceptedBySid.get(sid) || []; acc.push(text); _steerAcceptedBySid.set(sid, acc); } catch (_) {}
        if (curSid() === sid && has('_showSteerIndicator')) _showSteerIndicator(text0 || text);
        const why = { codex: 'Codex can’t take messages mid-run, so Tabbie reads it when Codex finishes',
          older: 'This run started before the update, so it’s read when the run ends. New runs take messages straight away',
          helper: 'Couldn’t reach Tabbie’s helper, so it’s read when this step ends' }[liveCache.sid === sid ? liveCache.reason : ''];
        toast(why || 'Sent into the current reply', why ? 5000 : 2200);
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
      markDelivered(ts);
      toast('The reply had finished, so it was sent as a new message', 3200);
      return;
    }
    putBack(sid, entry, index);
    let why = '';
    try { why = t(_steerFailureMessageKey(fb)); } catch (_) {}
    toast((why ? why + '. ' : 'Couldn’t push it in. ') + (workerTakes(sid) ? 'It stays queued and goes to Claude in a moment.' : 'It stays queued and sends after this reply.'), 4500, 'warning');
  }
  window.hxQueuePushNow = pushNow;

  // Upstream drains a queued message by writing it into the composer and sending it, which wiped
  // whatever the user was typing. Keep that draft (and its files) and put it back after the send.
  let draft = null;
  function keepDrafts() {
    const shift = window.shiftQueuedSessionMessage, send0 = window.send;
    if (typeof shift !== 'function' || typeof send0 !== 'function' || shift.__hxDraft) return;
    const wrappedShift = function () {
      const next = shift.apply(this, arguments);
      const box = document.getElementById('msg'), s = st();
      const files = (s && s.pendingFiles) || [];
      if (next && box && (box.value.trim() || files.length)) draft = { text: box.value, files: [...files], at: Date.now() };
      return next;
    };
    wrappedShift.__hxDraft = true;
    window.shiftQueuedSessionMessage = wrappedShift;
    window.send = function () {
      const d = draft && Date.now() - draft.at < 5000 ? draft : null;
      draft = null;
      const out = send0.apply(this, arguments);
      if (d) {
        const restore = () => {
          const box = document.getElementById('msg'), s = st();
          if (!box || box.value.trim()) return;  // the send didn't go out (upstream kept it) or the user typed again
          box.value = d.text;
          if (s) s.pendingFiles = d.files;
          try { autoResize(); renderTray(); } catch (_) {}
        };
        setTimeout(restore, 250);
        Promise.resolve(out).finally(() => setTimeout(restore, 0));
      }
      return out;
    };
  }
  keepDrafts();
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', keepDrafts);
  window.addEventListener('load', keepDrafts);

  // ── 2c. queued messages survive a reload ──────────────────────────────────
  // The queue is persisted ('hermes-queue-<sid>') but upstream never rebuilds it on load: it only puts
  // the first entry in an empty composer, and only if it's newer than the last assistant reply, which
  // a run that finished after the reload always is. Otherwise the entry sat orphaned in storage.
  // Snapshot it (at start-up, and before each loadSession, since upstream may clear it), then put it
  // back in the in-memory queue once that chat's messages are loaded; the forwarder / drain take over.
  const persisted = new Map(); // sid -> entries read from storage, not yet restored
  const restoredTs = new Set(); // _queued_at of entries brought back (for the idle hint)
  const PREFIX = 'hermes-queue-';
  function snapshot(sid) {
    const read = (store) => {
      try {
        for (let i = 0; i < store.length; i++) {
          const k = store.key(i);
          if (!k || !k.startsWith(PREFIX) || (sid && k !== PREFIX + sid)) continue;
          const v = JSON.parse(store.getItem(k) || '[]');
          if (Array.isArray(v) && v.length && !persisted.has(k.slice(PREFIX.length))) persisted.set(k.slice(PREFIX.length), v);
        }
      } catch (_) {}
    };
    read(window.sessionStorage); read(window.localStorage); // sessionStorage wins, as upstream reads it
  }
  snapshot(null);
  const msgText = (m) => {
    const c = m && m.content;
    if (typeof c === 'string') return c;
    return Array.isArray(c) ? c.map((p) => (p && (p.text || (typeof p === 'string' ? p : ''))) || '').join('\n') : '';
  };
  // Sent for real = its text is a user message from after it was queued (timestamps are seconds).
  function alreadySent(e, msgs) {
    const text = entryText(e);
    return msgs.some((m) => {
      if (!m || m.role !== 'user') return false;
      const ts = (m.timestamp || m._ts || 0) * 1000;
      if (ts && e._queued_at && ts < e._queued_at - 1000) return false;
      const body = msgText(m).trim();
      return body === text || (text.length > 8 && body.includes(text));
    });
  }
  function tryRestore(sid) {
    const pending = sid && persisted.get(sid);
    if (!pending) return;
    try { if (_loadingSessionId === sid) return; } catch (_) {} // messages not in yet
    const s = st();
    if (!s || curSid() !== sid || !Array.isArray(s.messages)) return;
    persisted.delete(sid);
    const q = liveQueue(sid);
    const have = new Set(q.map((e) => e && e._queued_at));
    const back = pending.filter((e) => e && entryText(e) && !have.has(e._queued_at) && !delivered.has(e._queued_at)
      && !alreadySent(e, s.messages));
    if (!back.length) return;
    // Upstream may have put the first one in the composer as a draft: it's queued again, so clear it.
    const box = document.getElementById('msg');
    if (box && back.some((e) => entryText(e) === box.value.trim())) {
      const text = box.value;
      box.value = '';
      try { _clearRestoredGoalContinuationDraft(); } catch (_) {}
      try { _clearComposerDraft(sid, text, []); } catch (_) {}
      try { autoResize(); } catch (_) {}
    }
    try {
      const live = _getSessionQueue(sid, true);
      back.forEach((e) => { live.push(Object.assign({}, e, { files: realFiles(e) })); restoredTs.add(e._queued_at); }); // stored Files are {} by now
      live.sort((a, b) => (a._queued_at || 0) - (b._queued_at || 0));
      _persistSessionQueueStorage(sid, live);
      delete _queueRenderKeys[sid];
    } catch (_) { return; }
    try { updateQueueBadge(sid); } catch (_) {}
    const n = back.length === 1 ? 'Your queued message is' : `${back.length} queued messages are`;
    toast(runningFor(sid) ? `${n} still queued` : `${n} still queued: use Send now to send`, 4000);
  }
  function hookLoadSession() {
    const orig = window.loadSession;
    if (typeof orig !== 'function' || orig.__hxqRestore) return false;
    const w = async function (sid) {
      if (sid) snapshot(String(sid));
      try { return await orig.apply(this, arguments); } finally { tryRestore(curSid()); }
    };
    w.__hxqRestore = true;
    window.loadSession = w;
    return true;
  }

  function dropDelivered(sid) {
    const q = sid ? liveQueue(sid) : [];
    if (!delivered.size || !q.some((e) => e && delivered.has(e._queued_at))) return;
    saveQueue(sid, q.filter((e) => !(e && delivered.has(e._queued_at)))); // re-renders the card
  }

  // Queued messages don't wait for the whole reply: while a Claude Code worker runs, each one is
  // handed to it (like typing ahead in Claude Code), oldest first. Ones with files keep waiting.
  let forwarding = false;
  setInterval(async () => {
    const sid = curSid();
    hookLoadSession(); // no-op once hooked
    tryRestore(sid); // start-up load that ran before this file was hooked in
    dropDelivered(sid);
    if (forwarding || !sid || !runningFor(sid)) return;
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
    // Text-only items go to a live Claude worker within seconds (the forwarder below), not after the reply.
    const toClaude = () => running && workerTakes(sid) && liveQueue(sid).some((e) => entryText(e) && !realFiles(e).length);
    const said = toClaude();
    hint.textContent = said ? 'goes to Claude in a moment' : running ? 'sends after this reply'
      : q.some((e) => e && restoredTs.has(e._queued_at)) ? 'kept from before the reload' : 'sends next';
    if (running && (liveCache.sid !== sid || Date.now() - liveCache.at >= 4000)) {
      workerOpen(sid).then(() => { if (hint.isConnected && toClaude() !== said) decorate(sid); });
    }

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
    armSwallow(e.target);
  }

  // ── 5. composer Enter fixes ────────────────────────────────────────────────
  // Queueing clears #msg synchronously inside upstream's keydown (a normal send clears it only after
  // an await), so a line break that still arrives for that Enter (a separate keypress/char event, as
  // CDP-driven input sends) lands in the now-empty box. Drop a bare newline right after such an Enter.
  let swallowUntil = 0;
  function armSwallow(box) { if (box && !box.value) swallowUntil = Date.now() + 400; }
  function onKeyAfter(e) { // bubble phase on document: after upstream's #msg handler
    if (e.key === 'Enter' && e.defaultPrevented && e.target && e.target.id === 'msg') armSwallow(e.target);
  }
  const isBreak = (e) => e.inputType === 'insertLineBreak' || e.inputType === 'insertParagraph'
    || (e.inputType === 'insertText' && /^[\r\n]+$/.test(e.data || ''));
  function onBeforeInput(e) {
    if (!e.target || e.target.id !== 'msg' || Date.now() > swallowUntil || e.target.value || !isBreak(e)) return;
    e.preventDefault();
    swallowUntil = 0;
  }
  function onInput(e) { // backstop when no cancelable beforeinput came first
    const box = e.target;
    if (!box || box.id !== 'msg' || Date.now() > swallowUntil || !box.value || box.value.trim()) return;
    box.value = '';
    swallowUntil = 0;
    try { autoResize(); } catch (_) {}
  }

  // Slash menu: Enter picks the highlighted command, so with "/limit" already typed it only closed
  // the menu and a 2nd Enter sent. When the text already is that command exactly, close the menu
  // (marked dismissed so a late refresh can't reopen it) and let upstream's handler send as usual.
  function itemCommand(item) {
    if (!item || item.classList.contains('cmd-item-path')) return null;
    const parent = item.querySelector('.cmd-item-parent'), sub = item.querySelector('.cmd-item-subarg');
    if (parent && sub) return `${parent.textContent.trim()} ${sub.textContent.trim()}`;
    const name = item.querySelector('.cmd-item-name');
    const first = name && name.firstChild;
    return first && first.nodeType === 3 ? first.textContent.trim() : null; // "/name" before the arg/badge spans
  }
  function onSlashEnter(e) {
    if (e.key !== 'Enter' || e.shiftKey || e.altKey || e.isComposing || e.keyCode === 229) return;
    const box = e.target;
    if (!box || box.id !== 'msg') return;
    const dd = document.getElementById('cmdDropdown');
    if (!dd || !dd.classList.contains('open')) return;
    const sendKey = window._sendKey || 'enter';
    if (sendKey === 'shift+enter') return;
    // Only where upstream's plain path would send this Enter (phones keep Enter = newline).
    const touch = has('_isTouchOnlyDevice') && _isTouchOnlyDevice();
    if ((sendKey === 'ctrl+enter' || touch) && !(e.ctrlKey || e.metaKey)) return;
    const items = dd.querySelectorAll('.cmd-item');
    const item = dd.querySelector('.cmd-item.selected') || (items.length === 1 ? items[0] : null);
    const cmd = itemCommand(item);
    const text = box.value || '';
    const at = has('_activeSlashCommandOffset') ? _activeSlashCommandOffset(text) : (text.startsWith('/') ? 0 : -1);
    if (!cmd || at < 0 || text.slice(at).trim() !== cmd) return;
    try { hideCmdDropdown(); markSlashDropdownDismissed(); } catch (_) { return; }
  }

  let tries = 0;
  (function init() {
    const ready = ['_renderQueueChips', 'updateSendBtn', 'send'].every(has);
    if (!ready) { if (++tries < 100) setTimeout(init, 100); return; }
    wrap('_renderQueueChips', (sid) => decorate(sid));
    wrap('updateSendBtn', () => { syncNowBtn(); busyPlaceholder(); });
    wrap('_applyBusyComposerPlaceholder', busyPlaceholder);
    // Upstream's own steer chip (Codex / hub path): plainer label than "Steer".
    wrap('_showSteerIndicator', () => {
      const b = document.querySelector('#msgInner .steer-indicator .steer-badge') // upstream keeps only one;
      if (b) b.textContent = 'Sent';
    });
    document.addEventListener('keydown', onKey, true);
    document.addEventListener('keydown', onSlashEnter, true);
    document.addEventListener('keydown', onKeyAfter);
    document.addEventListener('beforeinput', onBeforeInput, true);
    document.addEventListener('input', onInput, true);
    watchTranscript();
    hookLoadSession();
    // The start-up load may already be under way (unhooked): retry briefly until its messages land.
    const t0 = Date.now();
    const early = setInterval(() => { tryRestore(curSid()); if (!persisted.has(curSid()) || Date.now() - t0 > 15000) clearInterval(early); }, 250);
    const msg = document.getElementById('msg');
    if (msg) msg.addEventListener('input', syncNowBtn);
    syncNowBtn();
    const sid = curSid();
    if (sid) { try { delete _queueRenderKeys[sid]; updateQueueBadge(sid); } catch (_) {} }
  })();
})();
