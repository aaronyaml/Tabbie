/* Tabbie — Settings → Accounts → Team, and the "A new device wants in" sheet (plain JS, no build step).
 * Connects this Tabbie to a team relay: any server that answers /.well-known/tabbie-team with protocol tt1.
 * Tabbie knows no team by name: the name and address come from the relay you type in. The sidecar runs bin/tt
 * (team_link.py), which holds the keys; this page only ever sees names, fingerprints and states.
 *   Joining with an invite (the usual way): paste the code a teammate's Tabbie made. Tabbie connects, links this
 *   Mac and trusts the inviting Mac by itself; nothing to type anywhere else.
 *   Or by hand: 1. Connect: type the relay's address; Tabbie checks it really is a team relay.
 *   2. Link this Mac: Tabbie shows a code; sign in on the relay's site and enter it there.
 *   3. Share: pick which projects show live on your team's devices.
 *   Approving a device: you type the code that device shows. Tabbie never shows a waiting device's code.
 * No confirm()/alert(): disconnect confirms inline.
 */
(() => {
  'use strict';
  if (window.__hxTeam) return;
  window.__hxTeam = true;

  const API = 'api/extensions/cc-agents/sidecar/';
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const icon = (n, s) => (typeof window.hxIcon === 'function' ? window.hxIcon(n, s || 14) : '');
  const SNOOZE = 'hxTeamSnooze';
  let box = null, state = null, loading = false, err = '', busy = '', asking = '', pairTimer = 0, typedUrl = '', typedCode = '', typedInvite = '', typedFor = '', byHand = false, made = null, copied = false;

  async function get(force) {
    const r = await fetch(API + 'team' + (force ? '?force=1' : ''), { credentials: 'same-origin', cache: 'no-store' });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) { const e = new Error(j.error || String(r.status)); e.status = r.status; throw e; }
    return j;
  }
  async function post(path, body) {
    const r = await fetch(API + 'team/' + path, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok || j.ok === false) throw new Error(j.error || 'Something went wrong.');
    return j;
  }
  const relayLink = (st, label) => `<a class="hx-st-btn hx-team-link" href="${esc(st.relay)}" target="_blank" rel="noopener noreferrer">${esc(label)}${icon('LinkSquare02Icon', 13)}</a>`;
  const cap = (n) => String(n || '').replace(/^./, (c) => c.toUpperCase());
  const host = (u) => { try { return new URL(u).host; } catch (_) { return u; } };

  // ── Settings section ──────────────────────────────────────────────────────────────────────────────
  function connectCard() {
    // One box: the team site's link (default: then Get a code) or an invite code (TT1-…), whichever was sent.
    return `<div class="hx-st-card hx-team-card"><div class="hx-st-main">
      <b class="hx-team-title">Join your team</b>
      <span class="hx-st-dim">Paste your team’s link from the team site (like <b>https://team.example.com</b>), then press <b>Get a code</b> and type it on the site. An invite code (starts with <b>TT1-</b>) works here too.</span>
      <form class="hx-team-form hx-team-join" data-act="join" autocomplete="off">
        <textarea name="code" rows="2" placeholder="https://team.example.com" spellcheck="false" autocapitalize="off" autocomplete="off" required aria-label="Team link or invite code">${esc(typedInvite)}</textarea>
        <button type="submit" class="hx-st-btn is-primary"${busy ? ' disabled' : ''}>${busy === 'join' || busy === 'connect' ? 'Connecting…' : 'Connect'}</button>
      </form>
    </div></div>`;
  }
  function teamUrl(v) {  // any link on the team site works: keep its address only
    const t = String(v || '').trim();
    try { const u = new URL(/^https?:\/\//i.test(t) ? t : 'https://' + t); return u.origin; } catch (_) { return ''; }
  }

  function joiningCard(st) {
    const who = cap(st.pairing.inviter) || 'your teammate';
    return `<div class="hx-st-card hx-team-card"><div class="hx-st-main" aria-live="polite">
      <b class="hx-team-title"><span class="hx-team-spin" aria-hidden="true"></span>Almost done</b>
      <span class="hx-st-dim">You’re in the team. Now waiting for ${esc(who)}’s Mac to hand over the team key. It does that by itself within a minute, as long as it’s switched on with Tabbie running.</span>
      <span class="hx-st-dim">You can close this and carry on: it finishes in the background.</span>
    </div></div>
    <div class="hx-team-foot"><button type="button" class="hx-st-btn" data-act="disconnect-yes">Cancel and start again</button></div>`;
  }

  function needsInviteCard(st) {
    // signed in on a team that already exists: its key has to come from a teammate's Mac, by an invite code
    const mates = (st.pairing.teammates || []).map(cap);
    const who = mates.length ? mates.join(' or ') : 'a teammate';
    return `<div class="hx-st-card hx-team-card"><div class="hx-st-main">
      <b class="hx-team-title">One more step</b>
      <span class="hx-st-dim">You’re signed in. To see the team’s shared chats, this Mac needs the team’s key from ${esc(who)}. Ask them for an invite code: on their Mac, <b>Settings → Accounts → Team → Invite</b>. Paste it here.</span>
      <form class="hx-team-form hx-team-join" data-act="join" autocomplete="off">
        <textarea name="code" rows="2" placeholder="TT1-…" spellcheck="false" autocapitalize="off" autocomplete="off" required aria-label="Invite code">${esc(typedInvite)}</textarea>
        <button type="submit" class="hx-st-btn is-primary"${busy ? ' disabled' : ''}>${busy === 'join' ? 'Joining…' : 'Join'}</button>
      </form>
    </div></div>
    <div class="hx-team-foot"><button type="button" class="hx-st-btn" data-act="disconnect-yes">Use a different relay</button></div>`;
  }

  function inviteCard() {
    const open = (state && state.invites) || [];
    const shown = made ? `<div class="hx-team-code hx-team-inv" aria-live="polite">
        <span class="hx-st-dim">Send this to <b>${esc(cap(made.for))}</b> (message or email). In Tabbie they paste it into <b>Settings → Accounts → Team</b>. Works once, for 24 hours.</span>
        <code class="hx-team-invcode">${esc(made.code)}</code>
        <p class="hx-team-links"><button type="button" class="hx-st-btn is-primary" data-act="copy-invite">${copied ? 'Copied' : 'Copy code'}</button><button type="button" class="hx-st-btn" data-act="done-invite">Done</button></p>
      </div>` : `<form class="hx-team-form" data-act="invite" autocomplete="off">
        <input name="for" value="${esc(typedFor)}" placeholder="Their team name, like leo" spellcheck="false" autocapitalize="off" required aria-label="Teammate’s team name">
        <button type="submit" class="hx-st-btn is-primary"${busy ? ' disabled' : ''}>${busy === 'invite' ? 'Making…' : 'Make invite code'}</button>
      </form>`;
    const waiting = open.length && !made ? `<span class="hx-st-dim">Waiting for ${esc([...new Set(open.map((x) => cap(x.for)))].join(', '))} to use their code. Keep this Mac on so it can let them in.</span>` : '';
    return `<div class="hx-st-card hx-team-card"><div class="hx-st-main">
      <b>Invite a teammate</b>
      <span class="hx-st-dim">One code links their Tabbie to the team and to this Mac. They need a team account first.</span>
      ${shown}${waiting}
    </div></div>`;
  }

  function pairCard(st) {
    const p = st.pairing && st.pairing.state === 'waiting' ? st.pairing : null;
    const expired = st.pairing && st.pairing.state === 'expired';
    const body = p
      ? `<div class="hx-team-code" aria-live="polite"><span class="hx-st-dim">Enter this code on ${esc(host(st.relay))} under <b>Link your Mac</b></span>
           <code>${esc(p.code)}</code>
           <span class="hx-st-dim">The site then shows this Mac’s fingerprint, <code>${esc(p.fingerprint)}</code>. Check it matches before you confirm.</span></div>
         <p class="hx-team-links">${relayLink(st, 'Open ' + (st.name || 'the site'))}<span class="hx-st-dim hx-team-wait">Waiting for you to enter it…</span></p>`
      : `<span class="hx-st-dim">${expired ? 'That code ran out (they last 10 minutes). Get a new one.' : 'Sign in on the team’s site with your passkey first, then get a code here and enter it there.'}</span>
         <p class="hx-team-links"><button type="button" class="hx-st-btn is-primary" data-act="pair"${busy ? ' disabled' : ''}>${busy === 'pair' ? 'Asking…' : 'Get a code'}</button>${relayLink(st, 'Open ' + (st.name || 'the site'))}</p>`;
    return `<div class="hx-st-card hx-team-card"><div class="hx-st-main"><b>Link this Mac</b>${body}</div></div>`;
  }

  function waitingRows(st) {
    return (st.pending || []).map((d, i) => `<form class="hx-team-ask" data-act="approve" autocomplete="off">
        <div class="hx-st-main"><b>${esc(d.name)} wants in</b><span class="hx-st-dim">If that’s you, type the code it shows on screen.</span></div>
        <div class="hx-team-ask-f"><input name="code" value="${i === 0 ? esc(typedCode) : ''}" placeholder="ABCD-EFGH-JKLM" maxlength="14" spellcheck="false" autocapitalize="characters" required aria-label="Code shown on ${esc(d.name)}">
        <button type="submit" class="hx-st-btn is-primary">Approve</button></div></form>`).join('');
  }

  function linkedCards(st) {
    const shared = new Set((st.projects || []).map((p) => p.toLowerCase()));
    const projects = st.all_projects || [];
    const devs = (st.devices || []).map((d) => `<div class="hx-st-row"><div class="hx-st-main"><b>${esc(d.name)}</b><span class="hx-st-dim">Fingerprint <code>${esc(d.fingerprint)}</code></span></div></div>`).join('');
    const macs = (st.macs || []).map((m) => `<div class="hx-st-row"><div class="hx-st-main"><b>${esc(cap(m.owner))}’s Mac</b><span class="hx-st-dim">Fingerprint <code>${esc(m.fingerprint)}</code></span></div></div>`).join('');
    return `${(st.pending || []).length ? `<div class="hx-st-card hx-team-waiting">${waitingRows(st)}</div>` : ''}
      <div class="hx-st-card hx-st-list">
        <div class="hx-st-row"><div class="hx-st-main"><b>This Mac</b><span class="hx-st-dim">${esc(st.mac ? st.mac.name : '')} · fingerprint <code>${esc(st.mac ? st.mac.fingerprint : '')}</code></span></div>
          <div class="hx-st-side">${relayLink(st, 'Open site')}</div></div>
      </div>
      ${inviteCard()}
      <h4 class="hx-team-h">Shared projects</h4>
      <div class="hx-st-card hx-st-list">${projects.length ? projects.map((p) => `<div class="hx-st-row"><label class="hx-team-sw"><span class="hx-st-main"><b>${esc(p)}</b><span class="hx-st-dim">${shared.has(p.toLowerCase()) ? 'Live for your team' : 'Private'}</span></span>
          <input type="checkbox" class="hx-switch-input" data-act="share" data-project="${esc(p)}"${shared.has(p.toLowerCase()) ? ' checked' : ''}${busy === 'share:' + p ? ' disabled' : ''}></label></div>`).join('')
        : '<p class="hx-st-note hx-team-empty">No projects yet. Make one and it shows up here to share.</p>'}</div>
      <p class="hx-st-note">Every chat in a shared project shows live on your team’s devices: prompts, replies as they’re written, and progress. Chats in other projects never leave this Mac.</p>
      ${devs || macs ? `<h4 class="hx-team-h">Trusted devices</h4><div class="hx-st-card hx-st-list">${devs}${macs}</div>` : ''}
      <div class="hx-team-foot">${asking === 'disconnect'
        ? `<span class="hx-team-q">Disconnect? This Mac forgets its team keys; shared history stays on the relay. Remove this Mac on the site too.</span><button type="button" class="hx-st-btn is-danger" data-act="disconnect-yes">Disconnect</button><button type="button" class="hx-st-btn" data-act="cancel">Keep</button>`
        : '<button type="button" class="hx-st-btn" data-act="disconnect">Disconnect</button>'}</div>`;
  }

  function render() {
    if (!box) return;
    if (!state) {
      box.innerHTML = `<section class="hx-st-group"><h3>Team</h3>${err ? `<p class="hx-st-note">${esc(err)}</p><p class="hx-team-links"><button type="button" class="hx-st-btn" data-act="retry">Try again</button></p>` : '<div class="hx-st-card hx-st-skel"><i></i><i></i></div>'}</section>`;
      return;
    }
    const st = state;
    let body = '';
    if (!st.installed) body = '<p class="hx-st-note">Team isn’t available in this copy of Tabbie (bin/tt is missing). Update Tabbie to get it.</p>';
    else if (!st.relay) body = connectCard();
    else if (!st.linked && st.pairing && st.pairing.state === 'joining') body = joiningCard(st);
    else if (!st.linked && st.pairing && st.pairing.state === 'needs_invite') body = needsInviteCard(st);
    else if (!st.linked) body = pairCard(st) + `<div class="hx-team-foot"><button type="button" class="hx-st-btn" data-act="disconnect-yes">Use a different relay</button></div>`;
    else body = linkedCards(st);
    const pill = st.relay ? `<span class="hx-st-pill ${st.linked ? 'is-ok' : 'is-warn'}">${esc(st.name || host(st.relay))}${st.linked ? ' · ' + esc(cap(st.owner)) : (st.pairing && st.pairing.state === 'joining' ? ' · joining' : (st.pairing && st.pairing.state === 'needs_invite' ? ' · needs invite' : ' · not linked'))}</span>` : '';
    const f = box.contains(document.activeElement) && document.activeElement.name ? document.activeElement : null;
    const keep = f ? { name: f.name, form: f.form && f.form.dataset.act, a: f.selectionStart, b: f.selectionEnd } : null;
    box.innerHTML = `<section class="hx-st-group">
      <div class="hx-st-head"><h3>Team</h3>${pill}</div>
      ${err ? `<p class="hx-st-fix hx-team-err" role="alert">${esc(err)}</p>` : ''}
      ${st.relay_error ? `<p class="hx-st-fix hx-team-err">Couldn’t reach ${esc(st.name || 'the relay')}: ${esc(st.relay_error)}</p>` : ''}
      ${body}
    </section>`;
    if (keep) {  // a redraw never takes away what's being typed, or the place in it
      const el = box.querySelector(`form[data-act="${keep.form}"] [name="${keep.name}"]`) || box.querySelector(`[name="${keep.name}"]`);
      if (el) { el.focus({ preventScroll: true }); try { el.setSelectionRange(keep.a, keep.b); } catch (_) {} }
    }
    // while a code is showing, check every 3 s whether it has been entered
    clearTimeout(pairTimer);
    if (st.pairing && st.pairing.state === 'waiting' && box.dataset.on) pairTimer = setTimeout(checkPair, 3000);
    if (st.pairing && st.pairing.state === 'joining' && box.dataset.on) pairTimer = setTimeout(checkPair, 5000);
  }

  async function checkPair() {
    if (!box || !box.dataset.on) return;
    try {
      const r = await post('pair-check');
      state = r;
    } catch (e) { err = e.message; }
    render();
  }

  async function load(force, tries) {
    if (!box || (loading && !tries)) return;
    loading = true;
    try { state = await get(force); err = ''; }
    catch (e) {
      if ((!e.status || e.status >= 502) && (tries || 0) < 4) { setTimeout(() => load(force, (tries || 0) + 1), 1500); return; }
      err = e.status === 404 ? 'Tabbie’s helper is out of date: restart it to set up Team.'
        : e.status === 403 ? 'Allow Tabbie’s helper (Settings → Extensions) to set up Team.'
        : e.status === 400 && e.message ? e.message  // Team itself answered: say what it said
        : 'Couldn’t reach Tabbie’s helper. It may still be starting.';
    }
    loading = false;
    render();
  }

  async function act(name, fn) {
    err = ''; busy = name; render();
    try { await fn(); } catch (e) { err = e.message; }
    busy = '';
    render();
    sheetCheck();
  }

  function onClick(e) {
    const b = e.target.closest('[data-act]');
    if (!b || b.tagName === 'FORM' || b.tagName === 'INPUT') return;
    const a = b.dataset.act;
    if (a === 'pair') act('pair', async () => { state = await post('pair', {}); });
    else if (a === 'by-hand') { byHand = true; render(); const i = box.querySelector('input[name="url"]'); if (i) i.focus(); }
    else if (a === 'done-invite') { made = null; copied = false; render(); }
    else if (a === 'copy-invite' && made) {
      const done = () => { copied = true; render(); };
      if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(made.code).then(done, () => selectCode());
      else selectCode();
    }
    else if (a === 'disconnect') { asking = 'disconnect'; render(); }
    else if (a === 'retry') { err = ''; loading = false; render(); load(true); }
    else if (a === 'cancel') { asking = ''; render(); }
    else if (a === 'disconnect-yes') { asking = ''; act('disconnect', async () => { state = await post('disconnect'); }); }
  }
  function selectCode() {  // no clipboard (plain http): select it so Cmd-C copies
    const c = box.querySelector('.hx-team-invcode');
    if (!c) return;
    const r = document.createRange(); r.selectNodeContents(c);
    const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r);
  }
  function onChange(e) {
    const i = e.target.closest('input[data-act="share"]');
    if (!i) return;
    const p = i.dataset.project;
    act('share:' + p, async () => { state = await post('share', { project: p, on: i.checked }); });
  }
  function onInput(e) {  // remember what's typed, so a redraw puts it back
    const t = e.target;
    const act = t.form && t.form.dataset.act;
    if (act === 'join' && t.name === 'code') typedInvite = t.value;
    else if (act === 'connect' && t.name === 'url') typedUrl = t.value;
    else if (act === 'invite' && t.name === 'for') typedFor = t.value;
    else if (act === 'approve' && t.name === 'code') typedCode = t.value;
  }
  function onSubmit(e) {
    const f = e.target.closest('form[data-act]');
    if (!f) return;
    e.preventDefault();
    const data = Object.fromEntries(new FormData(f).entries());
    if (f.dataset.act === 'connect') { typedUrl = data.url; act('connect', async () => { state = await post('connect', { url: data.url }); typedUrl = ''; }); }
    else if (f.dataset.act === 'join') {
      typedInvite = data.code;
      const v = String(data.code || '').replace(/\s+/g, '');
      if (/^TT1-/i.test(v)) act('join', async () => { state = await post('join', { code: v }); typedInvite = ''; });
      else {
        const url = teamUrl(v);
        if (!url) { err = 'Paste the team site’s link, like https://team.example.com, or an invite code starting TT1-.'; render(); return; }
        act('connect', async () => {
          state = await post('connect', { url });
          typedInvite = '';
          if (state && !state.linked && !(state.pairing && state.pairing.code)) state = await post('pair', {});  // show the code straight away
        });
      }
    }
    else if (f.dataset.act === 'invite') { typedFor = data.for; act('invite', async () => { const r = await post('invite', { for: data.for }); made = r.result; copied = false; state = r; typedFor = ''; }); }
    else if (f.dataset.act === 'approve') { typedCode = data.code; act('approve', async () => { state = await post('approve', { code: data.code }); typedCode = ''; }); }
  }

  function mount() {
    const pane = document.getElementById('settingsPaneProviders');
    if (!pane) return;
    if (!box || !box.isConnected) {
      box = document.createElement('div');
      box.id = 'hxTeam';
      box.addEventListener('click', onClick);
      box.addEventListener('change', onChange);
      box.addEventListener('submit', onSubmit);
      box.addEventListener('input', onInput);
      pane.appendChild(box);
      render();
    }
    const sec = document.getElementById('hxSecrets');  // sits right after Secrets, at the end of Accounts
    // moving it takes focus out of whatever is being typed in and jumps the page: only when needed, never mid-typing
    if (sec && sec.nextElementSibling !== box && !box.contains(document.activeElement)) sec.after(box);
    const on = pane.classList.contains('active') && !document.hidden;
    if (on && !box.dataset.on) { box.dataset.on = '1'; load(true); }
    if (!on && box.dataset.on) { delete box.dataset.on; asking = ''; err = ''; clearTimeout(pairTimer); }
  }
  let watched = null;
  const mo = new MutationObserver(mount);
  setInterval(() => {
    const pane = document.getElementById('settingsPaneProviders');
    if (pane && pane !== watched) { watched = pane; mo.disconnect(); mo.observe(pane, { attributes: true, attributeFilter: ['class'] }); }
    mount();
  }, 800);
  document.addEventListener('visibilitychange', mount);
  mount();

  // ── "A new device wants in" sheet, anywhere in Tabbie ─────────────────────────────────────────────
  let sheet = null, sheetErr = '';
  const snoozed = () => { try { return Date.now() < Number(localStorage.getItem(SNOOZE) || 0); } catch (_) { return false; } };
  function closeSheet() {
    if (!sheet) return;
    const s = sheet; sheet = null;
    s.classList.remove('is-in');
    setTimeout(() => s.remove(), 220);
  }
  function drawSheet(st, keep) {
    const names = (st.pending || []).map((d) => d.name);
    if (!sheet) {
      sheet = document.createElement('div');
      sheet.className = 'hx-team-sheet';
      sheet.setAttribute('role', 'dialog');
      sheet.setAttribute('aria-modal', 'true');
      sheet.setAttribute('aria-labelledby', 'hxTeamSheetT');
      document.body.appendChild(sheet);
      sheet.addEventListener('submit', (e) => {
        e.preventDefault();
        const code = sheet.querySelector('input').value;
        const btn = sheet.querySelector('button[type="submit"]');
        btn.disabled = true; btn.textContent = 'Approving…';
        post('approve', { code }).then((r) => {
          state = r; render();
          if ((r.pending || []).length) { sheetErr = ''; drawSheet(r); } else closeSheet();
        }, (e2) => { sheetErr = e2.message; drawSheet(st, code); });
      });
      sheet.addEventListener('click', (e) => {
        if (e.target.closest('[data-later]') || e.target === sheet) {
          try { localStorage.setItem(SNOOZE, String(Date.now() + 10 * 60_000)); } catch (_) {}
          closeSheet();
        }
      });
      sheet.addEventListener('keydown', (e) => { if (e.key === 'Escape') sheet.querySelector('[data-later]').click(); });
      requestAnimationFrame(() => sheet && sheet.classList.add('is-in'));
    }
    sheet.innerHTML = `<form class="hx-team-sheet-c" autocomplete="off">
      <h2 id="hxTeamSheetT">A new device wants in</h2>
      <p><strong>${esc(names.join(', '))}</strong> wants to see your shared ${esc(st.name || 'team')} chats. If that’s you signing in on your phone or laptop, type the code it shows on screen.</p>
      <input name="code" value="${esc(keep || '')}" placeholder="ABCD-EFGH-JKLM" maxlength="14" spellcheck="false" autocapitalize="characters" autocomplete="off" required aria-label="Code the device shows">
      ${sheetErr ? `<p class="hx-team-sheet-err" role="alert">${esc(sheetErr)}</p>` : ''}
      <button type="submit" class="hx-st-btn is-primary">Approve</button>
      <button type="button" class="hx-team-later" data-later>Not now</button>
    </form>`;
    const i = sheet.querySelector('input');
    if (i) i.focus({ preventScroll: true });
  }
  async function sheetCheck() {
    if (document.hidden || snoozed()) return;
    let st = state;
    try { st = await get(false); } catch (_) { return; }
    // Settings → Team on screen already shows who's waiting, with its own code box: no sheet over it
    const inSettings = !!(box && box.dataset.on && box.getClientRects().length);
    if (box && box.dataset.on) { state = st; render(); }
    if (st.linked && (st.pending || []).length && !inSettings) { if (!sheet) { sheetErr = ''; drawSheet(st); } }
    else closeSheet();
  }
  setInterval(sheetCheck, 20_000);
  document.addEventListener('visibilitychange', sheetCheck);
  setTimeout(sheetCheck, 3000);
})();
