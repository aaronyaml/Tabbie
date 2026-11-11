/* Tabbie — sidebar laid out like ChatGPT's (plain JS, no build step). Pairs with sidebar.css.
 * Replaces the icon rail (desktop) and the drawer's icon row (phone). One left edge, one row height.
 *   Tabbie            [search]                 brand; search opens the chat filter
 *   New chat · Motion · Projects · Scheduled   (· Chats, when another panel is showing)
 *   Projects:  each project (tap filters the list to it, tap again for all) · New project
 *   Recents ⋯  the chats, title only. A dot only while a chat has something you haven't seen
 *              (upstream's unread / needs-attention state); "Needs you" when it waits on an answer.
 * Upstream's controls stay in the page, hidden: every row here clicks the original (new chat, project
 * chip, project "+", source tabs, "Show N archived"), so filtering, menus and state are upstream's own.
 * Right-click / long-press on a project row opens that chip's own menu (rename, colour, spaces…).
 * Forks sit right under the chat they came from (newest fork first), the family where its newest member is.
 *   [A] Aaron · Settings   bottom left: Settings, then every other panel (Skills, Memory, Logs…).
 */
(() => {
  'use strict';
  if (window.__hxSidebar) return;
  window.__hxSidebar = true;

  const icon = (n, s) => (typeof window.hxIcon === 'function' ? window.hxIcon(n, s) : '');
  const esc = (t) => String(t == null ? '' : t).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const $ = (s, r) => (r || document).querySelector(s);

  // Upstream's project chips, CLI/WebUI tabs and archived toggle are never shown: the rows above replace
  // them. They are taken out of the page the moment upstream adds them (a MutationObserver runs before the
  // browser paints), kept here detached so their own click handlers still work from our rows and menu.
  const stash = { bar: null, src: null, arch: null };
  const isArch = (c) => c.tagName !== 'A' && !c.classList.contains('session-item') && !c.classList.contains('hx-arch-row') && /^(show|hide)\s+(\d+\s+)?archived/i.test((c.textContent || '').trim());
  function pluck(list) {
    const got = new Set();
    for (const c of [...list.children]) {
      const k = c.classList.contains('project-bar') ? 'bar' : c.classList.contains('session-source-tabs') ? 'src' : isArch(c) ? 'arch' : '';
      if (!k) continue;
      stash[k] = c; c.remove(); got.add(k);
    }
    return got;
  }
  let listMo = null, watched = null;
  function watchList() {
    const list = $('#sessionList');
    if (!list || watched === list) return;
    watched = list;
    if (listMo) listMo.disconnect();
    // runs before the browser paints, so they never show. A full re-render (fresh rows added) that
    // didn't include one of them means upstream dropped it, so it's dropped here too.
    listMo = new MutationObserver((recs) => {
      const got = pluck(list);
      const rerender = recs.some((r) => [...r.addedNodes].some((n) => n.nodeType === 1 && (n.classList.contains('session-item') || !!n.querySelector('.session-item'))));
      if (rerender) for (const k of Object.keys(stash)) if (!got.has(k)) stash[k] = null;
      if (got.size || rerender) { if (nav) paint(); }
    });
    listMo.observe(list, { childList: true });
    pluck(list);
  }
  function chips() {
    const bar = stash.bar;
    if (!bar) return { bar: null, all: null, none: null, list: [], create: null };
    const all = [...bar.querySelectorAll('.project-chip')];
    return {
      bar,
      all: all.find((c) => !c.classList.contains('no-project') && !c.querySelector('span')) || null,
      none: all.find((c) => c.classList.contains('no-project')) || null,
      list: all.filter((c) => !c.classList.contains('no-project') && c.querySelector('span')),
      create: bar.querySelector('.project-create-btn'),
    };
  }
  const chipName = (c) => (c.querySelector('span:not(.color-dot)') || c).textContent.trim();
  function listExtras() {
    const tabs = stash.src ? [...stash.src.querySelectorAll('.session-source-tab')] : [];
    return { tabs, arch: stash.arch };
  }

  // ── the nav block above the list ─────────────────────────────────────────
  let nav = null;
  function ensureNav() {
    const panel = $('#panelChat');
    const search = panel && panel.querySelector(':scope > .session-search');
    const list = $('#sessionList');
    if (!panel || !list) return;
    watchList();
    if (!nav) {
      nav = document.createElement('nav');
      nav.className = 'hx-side';
      nav.setAttribute('aria-label', 'Chats');
      nav.addEventListener('click', onClick);
      nav.addEventListener('contextmenu', onContext);
    }
    // the search field shows when opened from the header, or while it holds text
    if (search) search.classList.toggle('hx-search-open', searchOpen || !!($('#sessionSearch') || {}).value);
    if (nav.parentNode !== panel || nav.nextElementSibling !== list) list.before(nav);
    paint();
  }

  function paint() {
    if (!nav) return;
    const c = chips();
    const { tabs, arch } = listExtras();
    const cli = tabs.find((t, i) => i > 0 && t.classList.contains('active'));
    const archOn = !!arch && /^hide/i.test(arch.textContent.trim());
    const noneOn = !!(c.none && c.none.classList.contains('active'));
    const dotOf = (ch) => { const d = ch.querySelector('.color-dot'); return (d && d.style.background) || ''; };
    const projects = c.list.map((ch, i) => {
      const on = ch.classList.contains('active'), col = dotOf(ch);
      return `
      <button type="button" class="hx-side-row hx-proj-row${on ? ' is-on' : ''}" data-act="proj" data-i="${i}" aria-pressed="${on}"${col ? ` style="--hx-proj:${esc(col)}"` : ''}>
        ${icon('Folder01Icon', 18)}<span>${esc(chipName(ch))}</span>${on ? `<i class="hx-proj-tick">${icon('Tick02Icon', 16)}</i>` : ''}</button>`;
    }).join('');
    // a project filter is on: the Recents header says so loudly, with one tap back to everything
    const act = c.list.find((ch) => ch.classList.contains('active'));
    const n = $('#sessionList') ? [...$('#sessionList').querySelectorAll('.session-item')].filter((r) => r.getClientRects().length).length : 0;
    const filter = act ? `
      <div class="hx-filter" style="--hx-proj:${esc(dotOf(act) || 'var(--hx-accent, #3054FD)')}" role="status">
        <span class="hx-filter-dot"></span>
        <span class="hx-filter-txt"><b>${esc(chipName(act))}</b><small>${n} chat${n === 1 ? '' : 's'} in this project</small></span>
        <button type="button" class="hx-filter-x" data-act="clearproj" aria-label="Show all chats">${icon('Cancel01Icon', 14)}<span>All chats</span></button>
      </div>` : '';
    const showing = cli ? cli.textContent.replace(/\s*\(\d+\)\s*$/, '') : noneOn ? 'Unassigned' : archOn ? 'With archived' : '';
    const html = `
      ${c.bar ? `<div class="hx-side-h">Projects</div>${projects}
      ${c.create ? `<button type="button" class="hx-side-row is-quiet" data-act="newproj">${icon('FolderAddIcon', 18)}<span>New project</span></button>` : ''}` : ''}
      <div class="hx-side-h hx-side-rec"><span>${act ? 'Chats' : showing ? `Recents · ${esc(showing)}` : 'Recents'}</span>
        <button type="button" class="hx-side-more${showing ? ' is-on' : ''}" data-act="menu" aria-haspopup="menu" aria-label="List options: other sessions, unassigned, archived"><span aria-hidden="true">⋯</span></button></div>${filter}`;
    if (nav.__html !== html) { nav.innerHTML = html; nav.__html = html; }
    rememberProject();
    archRow(arch);
  }
  // Archived chats: one plain row at the end of the list ("Archived chats · 25"), so they're findable
  function archRow(arch) {
    const list = $('#sessionList');
    if (!list) return;
    let row = list.querySelector(':scope > .hx-arch-row');
    const m = arch && (arch.textContent || '').trim().match(/^(show|hide)\s+(?:(\d+)\s+)?archived/i);
    if (!m) { if (row) row.remove(); return; }
    if (!row) {
      row = document.createElement('button');
      row.type = 'button';
      row.className = 'hx-side-row is-quiet hx-arch-row';
      row.addEventListener('click', () => { if (stash.arch) stash.arch.click(); setTimeout(paint, 60); });
    }
    const on = /^hide/i.test(m[1]);
    const html = `${icon(on ? 'ArrowUp01Icon' : 'Archive02Icon', 18)}<span>${on ? 'Hide archived chats' : `Archived chats${m[2] ? ` · ${m[2]}` : ''}`}</span>`;
    if (row.__html !== html) { row.innerHTML = html; row.__html = html; }
    if (row.parentNode !== list || row !== list.lastElementChild) list.append(row);
  }

  // ── the project selection: remembered across reloads and pages, and what a new chat starts in ──────────
  // localStorage keeps the sidebar's project (or "Unassigned", or All). With no chat open, the composer's
  // workspace (and so the new chat) follows it: the project's own folder, or the plain default for All.
  const PF = 'hx-proj-filter';
  let restored = false, baseWs = null, baseAsked = false;
  const activeProj = () => { try { return typeof _activeProject !== 'undefined' ? _activeProject : null; } catch (_) { return null; } };
  const allProjects = () => { try { return typeof _allProjects !== 'undefined' && Array.isArray(_allProjects) ? _allProjects : []; } catch (_) { return []; } };
  function rememberProject() {
    if (!restored) {
      let saved = null;
      try { saved = localStorage.getItem(PF); } catch (_) {}
      const list = allProjects();
      if (!saved) { restored = true; }
      else if (list.length || performance.now() > 8000) {
        restored = true;
        let none = '__none__';
        try { if (typeof NO_PROJECT_FILTER !== 'undefined') none = NO_PROJECT_FILTER; } catch (_) {}
        const ok = saved === none || list.some((x) => x.project_id === saved);
        if (ok && activeProj() !== saved && typeof _setActiveProjectFilter === 'function') _setActiveProjectFilter(saved);
        if (!ok) try { localStorage.removeItem(PF); } catch (_) {}
      }
      if (!restored) return;
    }
    const cur = activeProj();
    try { if (cur) localStorage.setItem(PF, String(cur)); else localStorage.removeItem(PF); } catch (_) {}
    newChatWorkspace(cur);
  }
  function newChatWorkspace(cur) {
    let sess = null;
    try { sess = typeof S !== 'undefined' ? S : null; } catch (_) {}
    if (!sess || sess.session) return;  // a chat is open: it keeps its own workspace
    if (baseWs === null) {
      if (!baseAsked) {
        baseAsked = true;
        fetch('api/settings', { credentials: 'same-origin' }).then((r) => r.json())
          .then((j) => { baseWs = (j && j.default_workspace) || sess._profileDefaultWorkspace || ''; newChatWorkspace(activeProj()); })
          .catch(() => { baseWs = sess._profileDefaultWorkspace || ''; });
      }
      return;
    }
    const proj = cur && allProjects().find((x) => x.project_id === cur);
    let ws = baseWs;
    if (proj && typeof _projectBindingsForNewSession === 'function') ws = (_projectBindingsForNewSession(proj) || {}).workspace || baseWs;
    if (ws && sess._profileDefaultWorkspace !== ws) {
      sess._profileDefaultWorkspace = ws;
      try { if (typeof syncWorkspaceDisplays === 'function') syncWorkspaceDisplays(); } catch (_) {}
    }
  }

  // ── persistent top block + account footer (outside the panels, so they stay on every panel) ──
  const NAME = ((window.HX_LOCAL || {}).name || '').trim();
  let top = null, foot = null, searchOpen = false;
  const currentPanel = () => { try { return typeof _currentPanel !== 'undefined' ? _currentPanel : 'chat'; } catch (_) { return 'chat'; } };
  const studio = () => document.documentElement.classList.contains('hx-studio-open');
  const projOpen = () => document.documentElement.classList.contains('hx-proj-open');
  function ensureFrame() {
    const sb = $('aside.sidebar');
    if (!sb) return;
    if (!top) {
      top = document.createElement('div');
      top.className = 'hx-side-frame-top';
      top.addEventListener('click', onClick);
    }
    const anchor = sb.querySelector(':scope > .panel-view');
    if (anchor && top.nextElementSibling !== anchor) anchor.before(top);
    const panel = currentPanel();
    const rows = [
      ['new', 'Edit02Icon', 'New chat', false],
      !document.documentElement.classList.contains('hx-no-motion') && ['motion', 'Video01Icon', 'Motion', studio()],
      ['projects', 'Briefcase01Icon', 'Projects', projOpen()],
      ['panel:tasks', 'Clock01Icon', 'Scheduled', panel === 'tasks'],
    ].filter(Boolean);
    const html = `<div class="hx-side-brand"><span class="hx-side-name">Tabbie</span>
        <button type="button" class="hx-side-ic" data-act="search" aria-label="Search chats" title="Search chats">${icon('Search01Icon', 18)}</button></div>
      <div class="hx-side-top">${rows.map(([act, ic, label, on]) => `<button type="button" class="hx-side-row${on ? ' is-on' : ''}" data-act="${act}"${on ? ' aria-current="page"' : ''}>${icon(ic, 18)}<span>${label}</span></button>`).join('')}</div>`;
    if (top.__html !== html) { top.innerHTML = html; top.__html = html; }
    // Another panel (Settings, Scheduled, Skills…): the same round Back button as Motion and Projects,
    // top left of the page.
    let back = $('.hx-panel-back');
    if (panel !== 'chat') {
      if (!back) {
        back = document.createElement('button');
        back.type = 'button';
        back.className = 'hx-back hx-panel-back hx-main-back';
        back.dataset.act = 'back';
        back.setAttribute('aria-label', 'Back to chats');
        back.innerHTML = `${icon('ArrowLeft01Icon', 18)}<span>Back</span>`;
        back.addEventListener('click', onClick);
      }
      // the page's own top-left corner (main area), never inside the sidebar; on phones the title bar
      // already holds the menu button, so Back sits beside it there instead of floating over the page
      const tb = innerWidth <= 640 ? $('.app-titlebar') : null;
      const host = tb && tb.getClientRects().length ? tb : $('main.main');
      if (host && back.parentNode !== host) host.prepend(back);
    } else if (back) back.remove();

    if (!foot) {
      foot = document.createElement('div');
      foot.className = 'hx-side-foot';
      foot.addEventListener('click', (e) => { const b = e.target.closest('[data-act="account"]'); if (b) { e.stopPropagation(); openAccount(b); } });
    }
    const resize = sb.querySelector(':scope > .resize-handle');
    if (foot.parentNode !== sb || (resize ? foot.nextElementSibling !== resize : sb.lastElementChild !== foot)) (resize ? resize.before(foot) : sb.appendChild(foot));
    const settingsOn = panel === 'settings';
    const fhtml = `<button type="button" class="hx-side-acct${settingsOn ? ' is-on' : ''}" data-act="account" aria-haspopup="menu" aria-label="Settings and more">
        <span class="hx-side-av" aria-hidden="true">${esc((NAME || 'T').slice(0, 1).toUpperCase())}</span>
        <span class="hx-side-who"><b>${esc(NAME || 'Tabbie')}</b><small>Settings</small></span>${icon('Settings02Icon', 18)}</button>`;
    if (foot.__html !== fhtml) { foot.innerHTML = fhtml; foot.__html = fhtml; }
  }
  // Every rail panel except Chat and Settings (Scheduled has its own row), as upstream shows them.
  function railExtras() {
    const rail = $('nav.rail');
    if (!rail) return [];
    return [...rail.querySelectorAll(':scope > .nav-tab')].filter((b) => {
      if (/^hx(Motion|Proj|More)Rail$/.test(b.id)) return false;
      if (['chat', 'settings', 'tasks'].includes(b.dataset.panel)) return false;
      if (!b.dataset.panel && !b.hasAttribute('data-dashboard-link')) return false;
      return !b.classList.contains('nav-tab-hidden') && b.style.display !== 'none';
    });
  }
  function openAccount(btn) {
    if (menu && menuFor === btn) return closeMenu();
    closeMenu();
    const items = [{ label: 'Settings', ic: 'Settings02Icon', go: () => window.switchPanel && window.switchPanel('settings', { fromRailClick: true }) }];
    railExtras().forEach((b, i) => {
      const svg = b.querySelector('svg');
      items.push({ label: b.getAttribute('aria-label') || b.dataset.tooltip || b.dataset.panel, svg: svg ? svg.outerHTML : '', go: () => b.click(), sep: i === 0 });
    });
    menu = document.createElement('div');
    menu.className = 'hx-more-menu hx-acct-menu';
    menu.setAttribute('role', 'menu');
    items.forEach((x) => {
      const it = document.createElement('button');
      it.type = 'button';
      it.className = 'hx-more-item' + (x.sep ? ' hx-sep' : '');
      it.setAttribute('role', 'menuitem');
      it.innerHTML = `<span class="hx-more-ic">${x.svg || icon(x.ic, 18)}</span><span>${esc(x.label)}</span>`;
      it.addEventListener('click', () => { closeMenu(); x.go(); afterNav(); });
      menu.appendChild(it);
    });
    document.body.appendChild(menu);
    const r = btn.getBoundingClientRect();
    if (innerWidth <= 640) menu.classList.add('is-sheet');
    else {
      menu.style.left = `${Math.round(r.left)}px`;
      menu.style.width = `${Math.round(Math.max(220, r.width))}px`;
      // anchored by its bottom edge just above the account button, so it stays above it whatever its rows
      // end up measuring (dropdowns.js restyles them after this)
      menu.style.top = 'auto';
      menu.style.bottom = `${Math.round(innerHeight - r.top + 6)}px`;
      menu.style.maxHeight = `${Math.round(Math.max(160, r.top - 14))}px`;
      menu.style.overflowY = 'auto';
      menu.style.transformOrigin = 'bottom left';
    }
    btn.setAttribute('aria-expanded', 'true');
    menuFor = btn;
    const first = menu.querySelector('.hx-more-item');
    if (first) first.focus({ preventScroll: true });
  }
  // On phones the sidebar is a drawer: close it after going somewhere.
  function afterNav() {
    setTimeout(() => { ensureFrame(); paint(); }, 60);
    if (innerWidth <= 640 && typeof window.dismissMobileSidebar === 'function') { try { window.dismissMobileSidebar(); } catch (_) {} }
  }

  // ── actions ──────────────────────────────────────────────────────────────
  function onClick(e) {
    const b = e.target.closest('[data-act]');
    if (!b) return;
    const act = b.dataset.act;
    if (act === 'new') {
      // from another panel (Settings, Scheduled…) go back to the chat view first: upstream's new-chat
      // button only resets the chat, it doesn't switch panels
      const go = () => { const n = $('#btnNewChat'); if (n) n.click(); afterNav(); };
      if (currentPanel() !== 'chat' && window.switchPanel) Promise.resolve(window.switchPanel('chat', { fromRailClick: true })).then(go, go);
      else go();
      return;
    }
    if (act === 'back' || act === 'chats') { if (window.switchPanel) window.switchPanel('chat', { fromRailClick: true }); afterNav(); return; }
    if (act === 'motion') { const m = $('#hxMotionRail') || $('#hxMotionTab'); if (m) m.click(); afterNav(); return; }
    if (act === 'projects') { const p = $('#hxProjRail') || $('#hxProjTab'); if (p) p.click(); afterNav(); return; }
    if (act.startsWith('panel:')) { if (window.switchPanel) window.switchPanel(act.slice(6), { fromRailClick: true }); afterNav(); return; }
    if (act === 'search') {
      if (currentPanel() !== 'chat' && window.switchPanel) window.switchPanel('chat', { fromRailClick: true });
      searchOpen = true; ensureNav();
      setTimeout(() => { const i = $('#sessionSearch'); if (i) i.focus(); }, 30);
      return;
    }
    const c = chips();
    if (act === 'proj') {
      const ch = c.list[+b.dataset.i];
      if (!ch) return;
      (ch.classList.contains('active') && c.all ? c.all : ch).click();
      setTimeout(paint, 50);
      return;
    }
    if (act === 'clearproj') { if (c.all) c.all.click(); setTimeout(paint, 50); return; }
    if (act === 'newproj') {
      // upstream's own inline "Project name" field, placed in our row instead of its hidden chip bar
      if (typeof _startProjectCreate === 'function') { _startProjectCreate(nav, b); const i = nav.querySelector('.project-create-input'); if (i) i.focus(); }
      else if (c.create) c.create.click();
      return;
    }
    if (act === 'menu') { e.stopPropagation(); openMenu(b); }
  }
  function onContext(e) {
    const b = e.target.closest('[data-act="proj"]');
    if (!b) return;
    const ch = chips().list[+b.dataset.i];
    if (!ch) return;
    e.preventDefault();
    const r = b.getBoundingClientRect();
    ch.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: e.clientX || r.left + 20, clientY: e.clientY || r.bottom, view: window }));
  }

  let menu = null, menuFor = null;
  function closeMenu() { if (menu) { const m = menu; if (window.hxPop && window.hxPop.close) window.hxPop.close(m, () => m.remove()); else m.remove(); menu = null; } if (menuFor) menuFor.setAttribute('aria-expanded', 'false'); menuFor = null; }
  function openMenu(btn) {
    if (menu && menuFor === btn) return closeMenu();
    closeMenu();
    const { tabs, arch } = listExtras();
    const c = chips();
    // Which chats: Tabbie chats or CLI sessions (pick one). Then two switches that stay open while you flip
    // them: chats without a project, archived chats. Picking another source turns archived off.
    const archOn = () => !!stash.arch && /^hide/i.test((stash.arch.textContent || '').trim());
    const radios = tabs.map((t) => ({ el: t, label: t.textContent.trim().replace(/^WebUI sessions/i, 'Tabbie chats'), on: t.classList.contains('active') }));
    const switches = [];
    if (c.none) switches.push({ key: 'none', label: 'Only chats without a project', on: c.none.classList.contains('active'),
      flip: () => { const x = chips(); const el = x.none && x.none.classList.contains('active') ? x.all : x.none; if (el) el.click(); } });
    if (arch) switches.push({ key: 'arch', label: 'Show archived chats', on: archOn(), flip: () => { if (stash.arch) stash.arch.click(); } });
    menu = document.createElement('div');
    menu.className = 'hx-more-menu hx-rec-menu';
    menu.setAttribute('role', 'menu');
    radios.forEach((x) => {
      const it = document.createElement('button');
      it.type = 'button';
      it.className = 'hx-more-item hx-radio' + (x.on ? ' is-on' : '');
      it.setAttribute('role', 'menuitemradio');
      it.setAttribute('aria-checked', String(!!x.on));
      it.innerHTML = `<span>${esc(x.label)}</span><i class="hx-tick">${x.on ? icon('Tick02Icon', 16) : ''}</i>`;
      it.addEventListener('click', () => {
        closeMenu();
        if (x.on) return;
        if (archOn()) stash.arch.click();
        x.el.click();
        setTimeout(paint, 80);
      });
      menu.appendChild(it);
    });
    switches.forEach((x, i) => {
      const it = document.createElement('button');
      it.type = 'button';
      it.className = 'hx-more-item hx-switch-item' + (i === 0 && radios.length ? ' hx-sep' : '');
      it.setAttribute('role', 'menuitemcheckbox');
      it.setAttribute('aria-checked', String(!!x.on));
      it.innerHTML = `<span>${esc(x.label)}</span><i class="hx-switch" aria-hidden="true"></i>`;
      it.addEventListener('click', () => {
        const on = it.getAttribute('aria-checked') !== 'true';
        it.setAttribute('aria-checked', String(on));
        x.flip();
        setTimeout(paint, 80);
      });
      menu.appendChild(it);
    });
    document.body.appendChild(menu);
    const r = btn.getBoundingClientRect();
    menu.style.left = `${Math.round(Math.max(8, Math.min(r.right - menu.offsetWidth, innerWidth - menu.offsetWidth - 8)))}px`;
    menu.style.top = `${Math.round(Math.min(r.bottom + 6, innerHeight - menu.offsetHeight - 8))}px`;
    btn.setAttribute('aria-expanded', 'true');
    menuFor = btn;
    const first = menu.querySelector('.hx-more-item');
    if (first) first.focus({ preventScroll: true });
  }
  document.addEventListener('pointerdown', (e) => { if (menu && !menu.contains(e.target) && !(menuFor && menuFor.contains(e.target))) closeMenu(); }, true);
  document.addEventListener('keydown', (e) => { if (menu && e.key === 'Escape') { const f = menuFor; closeMenu(); if (f) f.focus(); } });
  addEventListener('resize', closeMenu);

  // ── forks sit right under the chat they came from ────────────────────────
  // Each family (a chat, its forks, forks of those) is one block, placed where its most recently
  // active member would be; under each chat its forks are newest first, indented one step.
  let meta = new Map(), metaAt = 0, metaBusy = false;  // sid -> {parent, updated}
  function loadMeta(force) {
    if (metaBusy || (!force && Date.now() - metaAt < 15000)) return;
    metaBusy = true;
    fetch('api/sessions', { credentials: 'same-origin', cache: 'no-store' })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (!d) return;
        const m = new Map();
        (d.sessions || d || []).forEach((x) => { if (x && x.session_id) m.set(x.session_id, { parent: x.parent_session_id || '', updated: Number(x.last_message_at || x.updated_at || 0) }); });
        meta = m; metaAt = Date.now(); groupForks();
      })
      .catch(() => {})
      .then(() => { metaBusy = false; });
  }
  function groupForks() {
    const list = $('#sessionList');
    if (!list || !meta.size) return;
    const rows = [...list.querySelectorAll('.session-item[data-sid]')].filter((r) => !r.closest('.session-child-session'));
    if (rows.length < 2) return;
    const bySid = new Map(rows.map((r) => [r.dataset.sid, r]));
    const parentOf = (sid) => { const p = (meta.get(sid) || {}).parent; return p && p !== sid && bySid.has(p) ? p : ''; };
    const kids = new Map();
    rows.forEach((r) => { const p = parentOf(r.dataset.sid); if (p) { if (!kids.has(p)) kids.set(p, []); kids.get(p).push(r.dataset.sid); } });
    if (!kids.size) { rows.forEach((r) => r.classList.remove('hx-fork')); return; }
    const upd = (sid) => (meta.get(sid) || {}).updated || 0;
    const rootOf = (sid) => { const seen = new Set(); while (parentOf(sid) && !seen.has(sid)) { seen.add(sid); sid = parentOf(sid); } return sid; };
    rows.forEach((r) => r.classList.toggle('hx-fork', !!parentOf(r.dataset.sid)));
    rows.forEach((r) => { const d = depthOf(r.dataset.sid, parentOf); r.style.setProperty('--hx-fork-depth', String(d)); });
    const done = new Set();
    rows.forEach((first) => {
      const root = rootOf(first.dataset.sid);
      if (done.has(root) || !kids.has(root)) return;
      done.add(root);
      // the family in order: root, then each chat followed by its forks (newest first)
      const order = [];
      const walk = (sid, seen) => {
        if (seen.has(sid)) return; seen.add(sid); order.push(sid);
        (kids.get(sid) || []).slice().sort((a, b) => upd(b) - upd(a)).forEach((k) => walk(k, seen));
      };
      walk(root, new Set());
      // `first` is the family's highest row in the list (rows are newest first): the block goes there
      let at = first;
      const already = order.every((sid, i) => i === 0 ? bySid.get(sid) === at : bySid.get(order[i - 1]).nextElementSibling === bySid.get(sid));
      if (already) return;
      order.forEach((sid) => { const r = bySid.get(sid); if (r === at) return; at.after(r); at = r; });
      // the root itself may sit lower: put it first, then re-chain
      const head = bySid.get(order[0]);
      if (head !== first) {
        first.before(head);
        let prev = head;
        order.slice(1).forEach((sid) => { const r = bySid.get(sid); if (prev.nextElementSibling !== r) prev.after(r); prev = r; });
      }
    });
  }
  function depthOf(sid, parentOf) { let d = 0; const seen = new Set(); while (parentOf(sid) && !seen.has(sid) && d < 4) { seen.add(sid); sid = parentOf(sid); d++; } return d; }

  // ── rows waiting on you (question / plan to approve) ─────────────────────
  let inbox = new Map(), inboxAt = 0;
  function loadInbox() {
    if (Date.now() - inboxAt < 20000) return;
    inboxAt = Date.now();
    fetch('api/extensions/cc-agents/sidecar/inbox', { credentials: 'same-origin', cache: 'no-store' })
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => { if (!j) return; inbox = new Map((j.items || []).map((x) => [x.session, x.kind])); tagRows(); })
      .catch(() => {});
  }
  function tagRows() {
    document.querySelectorAll('#sessionList .session-item[data-sid]').forEach((row) => {
      const kind = inbox.get(row.dataset.sid);
      let tag = row.querySelector('.hx-need-tag');
      if (!kind) { if (tag) tag.remove(); return; }
      const txt = kind === 'plan' ? 'Approve' : 'Needs you';
      if (!tag) {
        tag = document.createElement('span');
        tag.className = 'hx-need-tag';
        const time = row.querySelector('.session-time');
        if (time) time.before(tag); else (row.querySelector('.session-title-row') || row).appendChild(tag);
      }
      if (tag.textContent !== txt) tag.textContent = txt;
    });
  }

  function searchLabel() {
    const i = $('#sessionSearch');
    if (i && i.placeholder !== 'Search chats') { i.removeAttribute('data-i18n-placeholder'); i.placeholder = 'Search chats'; }
  }
  // ── pages upstream draws only in the sidebar (Todos): shown as a real page in the main area instead,
  // with the chat list staying in the sidebar. The panel node itself moves (ids and upstream code keep
  // working) and goes back to its place when another page opens.
  let pageHost = null, moved = null, slot = null;
  const cap = (x) => x.charAt(0).toUpperCase() + x.slice(1);
  function sidePage() {
    const p = currentPanel();
    const main = $('main.main');
    if (!main) return;
    const want = p && p !== 'chat' && !document.getElementById('main' + cap(p)) ? document.getElementById('panel' + cap(p)) : null;
    if (moved && moved !== want) { if (slot && slot.parentNode) slot.replaceWith(moved); moved = null; slot = null; }
    if (want && moved !== want) {
      if (!pageHost) { pageHost = document.createElement('div'); pageHost.id = 'hxSidePage'; pageHost.className = 'main-view'; }
      if (pageHost.parentNode !== main) main.append(pageHost);
      slot = document.createComment('hx-side-page');
      want.replaceWith(slot);
      pageHost.append(want);
      moved = want;
    }
    document.documentElement.classList.toggle('hx-side-page', !!moved);
  }
  // panel switches only flip classes, which the shared page watcher ignores
  (function watchPanels() {
    const sb = $('aside.sidebar');
    if (!sb) return setTimeout(watchPanels, 200);
    let q = 0;
    new MutationObserver(() => { if (!q) q = setTimeout(() => { q = 0; sync(); }, 16); })
      .observe(sb, { attributes: true, attributeFilter: ['class'], subtree: true });
  })();

  // ── the chat list while it loads: placeholder rows and a word on what's happening, never a blank list ──
  let loadEl = null, loadTick = 0;
  function listLoading() {
    const list = $('#sessionList');
    const SL = window.__hxSessLoad;
    if (!list || !SL) return;
    const empty = !list.querySelector('.session-item');
    const show = empty && !SL.done && (SL.pending > 0 || !SL.failed);
    if (!show) { if (loadEl) { loadEl.remove(); loadEl = null; } clearInterval(loadTick); loadTick = 0; return; }
    if (!loadEl) {
      loadEl = document.createElement('div');
      loadEl.className = 'hx-list-loading';
      loadEl.setAttribute('role', 'status');
      loadEl.innerHTML = `${'<i></i>'.repeat(6)}<p></p>`;
    }
    const secs = SL.since ? (Date.now() - SL.since) / 1000 : 0;
    const msg = SL.failed ? 'Couldn’t load your chats. Retrying…'
      : secs > 20 ? 'Still going. The first load after a restart reads every Claude Code session once; it’s quick after that.'
      : secs > 4 ? 'Getting your chats ready (first load after a restart)…' : 'Loading chats…';
    const p = loadEl.querySelector('p');
    if (p.textContent !== msg) p.textContent = msg;
    if (loadEl.parentNode !== list.parentNode || loadEl.nextElementSibling !== list) list.before(loadEl);
    if (!loadTick) loadTick = setInterval(listLoading, 1000);
  }
  addEventListener('hx-sessload', () => requestAnimationFrame(listLoading));

  function sync() { listLoading(); sidePage(); ensureFrame(); ensureNav(); searchLabel(); loadInbox(); tagRows(); loadMeta(); groupForks(); }
  // the filter closes again when it's emptied and loses focus
  document.addEventListener('focusout', (e) => {
    if (e.target && e.target.id === 'sessionSearch' && !e.target.value) setTimeout(() => { searchOpen = false; ensureNav(); }, 120);
  });
  document.documentElement.classList.add('hx-gpt-side');
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', sync); else sync();
  if (window.hxDom) window.hxDom.on(sync); else setInterval(sync, 1000);
  addEventListener('hx-motion-ready', () => ensureFrame());
  // Chip/tab clicks only flip classes: repaint the highlight shortly after any click in the sidebar.
  document.addEventListener('click', (e) => { if (e.target.closest && e.target.closest('.sidebar, .rail, .hx-more-menu')) setTimeout(() => { ensureFrame(); paint(); }, 80); }, true);
})();
