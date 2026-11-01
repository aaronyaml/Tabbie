/* Tabbie — new-chat home (plain JS, no build step). Pairs with home.css.
 * Like Claude / ChatGPT: a time-of-day greeting next to the buddy, the composer in the middle and
 * a row of "Try" cards underneath it (above it on phones), plus recent chats on desktop. Everything
 * under the composer fades away as soon as you start typing and comes back if you clear it.
 * Cards fill the composer (or run a slash command) rather than sending a canned prompt.
 */
(function () {
  'use strict';
  const NAME = (window.HX_LOCAL || {}).name || '';  // your first name, from local.js
  const CARDS = [
    { icon: 'Video01Icon', title: 'Make a product film', sub: 'An Apple-style motion ad for one of your apps', fill: '/motion ' },
    { icon: 'Brain01Icon', title: 'Plan a big change', sub: 'Astra plans it, you approve, Claude builds it', fill: '@astra ' },
    { icon: 'CodeIcon', title: 'Build with Claude', sub: 'Edit code in any of your projects', fill: '@claude ' },
    { icon: 'Analytics01Icon', title: 'Check my limits', sub: 'Claude, ChatGPT and credits left', fill: '/limit', send: true },
    { icon: 'KanbanIcon', title: 'Show my board', sub: 'Cards and todos you can edit in chat', fill: '/board', send: true },
    { icon: 'Note01Icon', title: 'Remember something', sub: 'Save a note Tabbie keeps for later', fill: 'Remember that ' },
  ];
  const icon = (n, s) => (typeof window.hxIcon === 'function' ? window.hxIcon(n, s) : '');
  const esc = (t) => String(t).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const phone = () => matchMedia('(max-width: 640px)').matches;

  function greeting() {
    const h = new Date().getHours();
    const part = h < 5 ? 'Up late' : h < 12 ? 'Morning' : h < 18 ? 'Afternoon' : 'Evening';
    return NAME ? `${part}, ${NAME}` : part;
  }
  function setGreeting() {
    const h2 = document.querySelector('#emptyState h2');
    if (!h2) return;
    const want = greeting();
    if (h2.textContent !== want) {
      const [part, name] = want.split(', ');
      h2.innerHTML = `${esc(part)}, <span class="hx-name">${esc(name)}</span>`;
    }
  }

  // Three cards per new chat, stable for that chat so re-renders don't reshuffle them.
  let pickedFor = null, picked = [];
  function pick() {
    let sid = '';
    try { sid = (S.session && S.session.session_id) || ''; } catch (_) {}
    if (pickedFor !== sid || !picked.length) {
      pickedFor = sid;
      picked = CARDS.map((c) => [Math.random(), c]).sort((a, b) => a[0] - b[0]).slice(0, 3).map((x) => x[1]);
    }
    return picked;
  }

  function fillComposer(card) {
    const ta = document.getElementById('msg');
    if (!ta) return;
    ta.value = card.fill;
    ta.dispatchEvent(new Event('input', { bubbles: true }));
    if (card.send) {
      const send = document.getElementById('btnSend');
      if (send) { send.click(); return; }
    }
    ta.focus();
    try { ta.setSelectionRange(ta.value.length, ta.value.length); } catch (_) {}
  }

  const ago = (sec) => {
    const d = Date.now() / 1000 - sec;
    if (d < 3600) return `${Math.max(1, Math.round(d / 60))}m ago`;
    if (d < 86400) return `${Math.round(d / 3600)}h ago`;
    if (d < 172800) return 'yesterday';
    return `${Math.round(d / 86400)}d ago`;
  };
  let recent = [], recentAt = 0;
  async function loadRecent() {
    if (Date.now() - recentAt < 30000) return;
    recentAt = Date.now();
    try {
      const r = await fetch('api/sessions', { credentials: 'same-origin' });
      const d = await r.json();
      const list = (d.sessions || d || []).filter((s) => s && s.title && (s.message_count || 0) > 0 && !s.archived
        && !(window.hxMotion && window.hxMotion.hides && window.hxMotion.hides(s))); // Motion studio sessions live in the studio
      list.sort((a, b) => (b.updated_at || b.last_message_at || 0) - (a.updated_at || a.last_message_at || 0));
      recent = list.slice(0, 12);
      render();
    } catch (_) {}
  }

  let home = null;
  function render() {
    if (!home) return;
    const cards = pick().map((c, i) => `
      <button type="button" class="hx-try" data-i="${i}">
        <span class="hx-try-top"><span class="hx-try-ic">${icon(c.icon, 20)}</span><span class="hx-try-pill">Try</span></span>
        <span class="hx-try-tt">${esc(c.title)}</span><span class="hx-try-sub">${esc(c.sub)}</span>
      </button>`).join('');
    const shown = recent.filter((s) => !(window.hxMotion && window.hxMotion.hides && window.hxMotion.hides(s))).slice(0, 3);
    const rec = !phone() && shown.length ? `
      <div class="hx-home-head"><span>Jump back in</span></div>
      <div class="hx-recent">${shown.map((s) => `
        <button type="button" class="hx-rec" data-sid="${esc(s.session_id)}">
          <span class="hx-rec-ic">${icon('BubbleChatIcon', 16)}</span>
          <span class="hx-rec-tt">${esc(s.title)}</span>
          <span class="hx-rec-when">${esc(ago(s.updated_at || s.last_message_at || 0))}</span>
        </button>`).join('')}</div>` : '';
    const html = `<div class="hx-home-head"><span>${icon('BulbIcon', 16)} Try Tabbie</span></div><div class="hx-tries">${cards}</div>${rec}`;
    if (home.dataset.html !== html) { home.innerHTML = html; home.dataset.html = html; }
  }

  function place() {
    const wrap = document.getElementById('composerWrap');
    if (!wrap) return;
    if (!home) {
      home = document.createElement('section');
      home.id = 'hxHome';
      home.setAttribute('aria-label', 'Suggestions');
      home.addEventListener('click', (e) => {
        const t = e.target.closest('.hx-try');
        if (t) return fillComposer(pick()[+t.dataset.i]);
        const r = e.target.closest('.hx-rec');
        if (r && typeof loadSession === 'function') loadSession(r.dataset.sid);
      });
    }
    // Under the composer on desktop, just above it on phones.
    if (phone()) { if (home.nextElementSibling !== wrap) wrap.before(home); }
    else if (wrap.nextElementSibling !== home) wrap.after(home);
    render();
  }

  function syncTyping() {
    const ta = document.getElementById('msg');
    document.documentElement.classList.toggle('hx-typing', !!(ta && ta.value.trim()));
  }

  function tick() { setGreeting(); place(); syncTyping(); }
  function start() {
    tick(); loadRecent();
    addEventListener('input', (e) => { if (e.target && e.target.id === 'msg') syncTyping(); }, true);
    addEventListener('resize', place);
    const es = document.getElementById('emptyState');
    if (es) new MutationObserver(() => { tick(); loadRecent(); }).observe(es, { attributes: true, childList: true, subtree: true, characterData: true });
    setInterval(tick, 1500); // composer cleared by sending, session switches, i18n re-renders
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
