/* Tabbie — Projects: first-class multi-folder projects with their own memory and Dreamer (plain JS).
 *
 * A Project (Acme = several repos) lives in the hmem registry on the computer. This panel talks to it
 * only through the cc-agents sidecar, which the WebUI proxies behind its own login + per-extension
 * token (api/extensions/cc-agents/sidecar/projects/*). Folder access is explicit: the picker lists
 * folder names on the computer (never files), and attaching shows the real path to confirm first.
 *
 * Each Project also gets one native WebUI project bound to its workspace (~/hermes/workspaces/<id>,
 * symlinks to its folders), so "New task" opens a chat that the router already knows is that project.
 *
 * Opens from the rail/drawer entry under Chat, like the motion studio. All data is rendered with
 * textContent; nothing from the server is ever inserted as HTML.
 */
(function () {
  'use strict';
  if (window.__hxProjects) return;
  window.__hxProjects = true;

  const API = 'api/extensions/cc-agents/sidecar';
  const icon = (n, s) => (typeof window.hxIcon === 'function' ? window.hxIcon(n, s || 18) : '');
  const phone = () => window.matchMedia('(max-width: 640px)').matches;

  // ── tiny DOM helper (text only) ──
  function h(tag, props, ...kids) {
    const n = document.createElement(tag);
    if (props) for (const [k, v] of Object.entries(props)) {
      if (v == null || v === false) continue;
      if (k === 'class') n.className = v;
      else if (k === 'text') n.textContent = v;
      else if (k === 'icon') n.insertAdjacentHTML('afterbegin', icon(v, props.size)); // our own bundled SVGs only
      else if (k === 'size') continue;
      else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
      else if (k === 'value') n.value = v;
      else n.setAttribute(k, v === true ? '' : v);
    }
    for (const k of kids.flat()) if (k != null && k !== false) n.append(k.nodeType ? k : String(k));
    return n;
  }
  const btn = (cls, label, on, ico, extra) => h('button', { type: 'button', class: cls, onclick: on, ...(extra || {}) },
    ico ? h('span', { class: 'hxp-i', icon: ico, size: 16 }) : null, label ? h('span', { text: label }) : null);
  const ago = (iso) => {
    if (!iso) return 'never';
    const s = (Date.now() - Date.parse(iso)) / 1000;
    if (s < 60) return 'just now';
    if (s < 3600) return `${Math.round(s / 60)} min ago`;
    if (s < 86400) return `${Math.round(s / 3600)} h ago`;
    if (s < 86400 * 30) return `${Math.round(s / 86400)} d ago`;
    return new Date(iso).toLocaleDateString();
  };
  const many = (w) => (/[^aeiou]y$/.test(w) ? w.slice(0, -1) + 'ies' : w + 's');  // memory → memories
  const word = (n, w) => (n === 1 ? w : many(w));
  const plural = (n, w) => `${n} ${word(n, w)}`;
  function toast(msg, kind) { try { if (typeof showToast === 'function') return showToast(msg, kind); } catch (_) {} console.log('[projects]', msg); }

  async function call(path, body) {
    const opts = { credentials: 'same-origin', cache: 'no-store', headers: { 'Content-Type': 'application/json' } };
    if (body !== undefined) { opts.method = 'POST'; opts.body = JSON.stringify(body); }
    const r = await fetch(`${API}${path}`, opts);
    const j = await r.json().catch(() => ({}));
    if (r.status === 401 || r.status === 403) { if (window.hxHelperConsent) window.hxHelperConsent.open(); }
    if (r.status === 401 || r.status === 403) throw Object.assign(new Error('Tabbie needs your OK to reach its helper. Press Allow on the card at the bottom right (or Settings → Extensions).'), { auth: true });
    if (r.status === 409) throw Object.assign(new Error(j.error || 'Changed elsewhere; reloaded.'), { conflict: true });
    if (!r.ok) throw new Error(j.error || `Request failed (${r.status})`);
    return j;
  }
  const get = (path, q) => call(`${path}${q ? '?' + new URLSearchParams(q) : ''}`);

  // WebUI's own API (same-origin; upstream helper when present)
  async function webui(path, body) {
    if (typeof api === 'function') return api(path, body === undefined ? {} : { method: 'POST', body: JSON.stringify(body) });
    const r = await fetch(path.replace(/^\//, ''), { method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || r.statusText);
    return j;
  }

  // ── state ──
  const S = { view: 'list', pid: null, tab: 'folders', p: null, list: null, archived: false, err: '', busy: false,
              cache: {}, sheet: null, poll: null };
  let root = null;

  // ── panel mount (same pattern as the motion studio) ──
  function place() {
    if (!root) return;
    const rail = document.querySelector('.rail');
    const r = rail && rail.getBoundingClientRect();
    root.style.left = `${r && r.width > 4 && !phone() ? Math.round(r.right) : 0}px`;
  }
  const isOpen = () => document.documentElement.classList.contains('hx-proj-open');
  function open(opts) {
    if (!root) {
      root = h('div', { id: 'hxProjects', role: 'region', 'aria-label': 'Projects' });
      document.body.appendChild(root);
    }
    if (window.hxStudio && window.hxStudio.isOpen && window.hxStudio.isOpen()) window.hxStudio.close({ keepChat: true });
    document.documentElement.classList.add('hx-proj-open');
    place();
    if (opts && opts.pid) { S.view = 'project'; S.pid = opts.pid; }
    else if (bootRoute) { S.view = 'project'; S.pid = bootRoute.pid; S.tab = bootRoute.tab; }
    bootRoute = null;
    syncNav();
    S.view === 'project' && S.pid ? loadProject(S.pid) : loadList();
  }
  function close() {
    document.documentElement.classList.remove('hx-proj-open');
    S.sheet = null;
    stopPoll();
    syncNav();
  }

  // True when the click is on the rail/drawer tab of the panel already showing under an overlay
  // (Projects, Motion studio). Upstream treats that as "collapse the sidebar"; overlays swallow it.
  window.hxSameTab = window.hxSameTab || ((t) => {
    const tab = t.closest('.rail .nav-tab[data-panel], .sidebar-nav .nav-tab[data-panel]');
    let cur = 'chat';
    try { if (typeof _currentPanel !== 'undefined' && _currentPanel) cur = _currentPanel; } catch (_) {}
    let desktop = true;  // desktop: keep the sidebar as the user left it (open or collapsed); phone: let the drawer close
    try { desktop = _isDesktopWidth(); } catch (_) {}
    return !!tab && tab.dataset.panel === cur && desktop;
  });

  // ── nav entries (rail + drawer), right under Motion / Chat ──
  function navButtons() {
    let made = false;
    const anchorRail = document.getElementById('hxMotionRail') || document.querySelector('.rail .rail-btn.nav-tab[data-panel="chat"]');
    if (anchorRail && !document.getElementById('hxProjRail')) {
      const b = h('button', { id: 'hxProjRail', type: 'button', class: 'rail-btn nav-tab has-tooltip hxp-nav', 'data-tooltip': 'Projects', 'aria-label': 'Projects' });
      b.innerHTML = icon('Briefcase01Icon', 20);
      anchorRail.after(b);
      made = true;
    }
    const anchorSide = document.getElementById('hxMotionTab') || document.querySelector('.sidebar-nav .nav-tab[data-panel="chat"]');
    if (anchorSide && !document.getElementById('hxProjTab')) {
      const b = h('button', { id: 'hxProjTab', type: 'button', class: 'nav-tab has-tooltip has-tooltip--bottom hxp-nav', 'data-label': 'Projects', 'data-tooltip': 'Projects', 'aria-label': 'Projects' });
      b.innerHTML = icon('Briefcase01Icon', 18);
      anchorSide.after(b);
      made = true;
    }
    if (made && typeof paintBadge === 'function') paintBadge();  // a re-created entry gets its count back
  }
  function syncNav() {
    navButtons();
    document.querySelectorAll('.hxp-nav').forEach((b) => {
      b.classList.toggle('hxp-on', isOpen());
      b.setAttribute('aria-current', isOpen() ? 'page' : 'false');
    });
    paintBadge();
  }

  // ── "to review" badge on the nav entries: Dreamer proposals waiting across active projects ──
  // Every 5 min, on coming back to the tab, and straight after anything done in the panel. Quiet:
  // a 401 here never opens the helper-consent card (only the panel itself asks).
  let toReview = 0, badgeT = 0, badgeAt = 0;
  function paintBadge() {
    document.querySelectorAll('.hxp-nav').forEach((b) => {
      let c = b.querySelector('.hxp-count');
      if (toReview > 0) {
        if (!c) { c = h('span', { class: 'hxp-count', 'aria-hidden': 'true' }); b.append(c); }
        const t = toReview > 99 ? '99+' : String(toReview);
        if (c.textContent !== t) c.textContent = t;  // unchanged: no DOM write (the nav MutationObserver watches the body)
        b.setAttribute('aria-label', `Projects, ${toReview} to review`);
      } else {
        if (c) c.remove();
        b.setAttribute('aria-label', 'Projects');
      }
    });
  }
  const sumPending = (list) => (list || []).reduce((n, p) => n + (Number((p.summary || {}).pending) || 0), 0);
  async function refreshBadge() {
    badgeAt = Date.now();
    try {
      const r = await fetch(`${API}/projects`, { credentials: 'same-origin', cache: 'no-store' });
      if (!r.ok) return;
      const j = await r.json();
      toReview = sumPending(j.projects);
      paintBadge();
    } catch (_) {}
  }
  const badgeSoon = () => { clearTimeout(badgeT); badgeT = setTimeout(refreshBadge, 400); };

  document.addEventListener('click', (e) => {
    const t = e.target;
    if (!t || !t.closest) return;
    if (t.closest('.hxp-nav')) {
      e.preventDefault();
      e.stopPropagation();
      try { if (typeof closeMobileSidebar === 'function') closeMobileSidebar(); } catch (_) {}
      S.view = 'list'; S.pid = null; S.sheet = null;
      open();
      return;
    }
    if (isOpen() && t.closest('.rail .nav-tab, .sidebar-nav .nav-tab, #btnNewChat, #btnTitlebarNewChat, .session-item, .hx-rec')) {
      close();
      if (window.hxSameTab(t)) { e.preventDefault(); e.stopPropagation(); }  // see motion.js: no sidebar collapse
    }
  }, true);
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape' || !isOpen()) return;
    if (S.sheet) up(); else if (S.view === 'project') toList(); else close();
  });
  addEventListener('resize', place);

  // ── address: /projects#<project id>[/<tab>], so a reload (or a pasted link) reopens the same view ──
  // A hash rather than /projects/<id>: Caddy serves the app shell for /projects itself, and routes.js
  // keeps the hash while Projects is open. Opening a project adds a history entry (Back returns to the
  // list); switching tabs replaces it.
  let bootRoute = null;  // read once at boot, applied by the first open()
  function fromHash(hash) {
    const m = /^#([^/]+)(?:\/([a-z]+))?$/.exec(hash || '');
    if (!m) return null;
    let pid;
    try { pid = decodeURIComponent(m[1]); } catch (_) { return null; }
    return { pid, tab: TABS.some((t) => t[0] === m[2]) ? m[2] : 'folders' };
  }
  const hashNow = () => (S.view === 'project' && S.pid ? `#${encodeURIComponent(S.pid)}${S.tab !== 'folders' ? '/' + S.tab : ''}` : '');
  const onPage = () => isOpen() && /^\/projects\/?$/.test(location.pathname);
  function addr() {
    if (!onPage()) return;  // still on the chat's address: routes.js moves to /projects and keeps the hash
    const want = hashNow();
    if (location.hash === want) return;
    const url = location.pathname + location.search + want;
    const wasProject = !!fromHash(location.hash);
    const H = window.__hxMoHist;  // motion-boot.js's unwrapped history calls (its wrapper may turn a push into a replace)
    if (want && !wasProject) H ? H.push({ hxProj: true }, url) : history.pushState({ hxProj: true }, '', url);
    else H ? H.replace(history.state, url) : history.replaceState(history.state, '', url);
  }
  function toList() {
    S.sheet = null;
    if (onPage() && fromHash(location.hash) && history.state && history.state.hxProj) history.back();  // popstate loads the list
    else { S.p = null; loadList(); }
  }
  addEventListener('popstate', () => {
    if (!onPage()) return;
    const r = fromHash(location.hash);
    if (r && S.view === 'project' && S.pid === r.pid) { if (S.tab !== r.tab) tab(r.tab); }
    else if (r) { S.sheet = null; S.tab = r.tab; loadProject(r.pid); }
    else if (S.view === 'project') { S.sheet = null; S.p = null; loadList(); }
  });

  // ── data ──
  async function loadList() {
    S.view = 'list'; S.err = '';
    addr();
    render();
    try {
      S.list = (await get('/projects', S.archived ? { archived: '1' } : null)).projects;
      if (!S.archived) { toReview = sumPending(S.list); badgeAt = Date.now(); paintBadge(); }
    }
    catch (e) { S.err = e.message; }
    render();
  }
  async function loadProject(pid, quiet) {
    S.view = 'project'; S.pid = pid; if (!quiet) S.err = '';
    addr();
    if (!quiet) render();
    try { S.p = await get('/projects/get', { id: pid }); S.cache = {}; }
    catch (e) { S.err = e.message; }
    render();
    loadTab();
  }
  async function loadTab(force) {
    const tab = S.tab, pid = S.pid;
    if (!pid || (S.cache[tab] && !force)) return render();
    const paths = { sessions: '/projects/sessions', memory: '/projects/memory', dreamer: '/projects/dreamer',
                    skills: '/projects/skills', architecture: '/projects/architecture' };
    if (!paths[tab]) return render();
    try {
      const d = await get(paths[tab], { id: pid });
      if (S.pid === pid) S.cache[tab] = d;
      if (tab === 'dreamer' && (d.running || starting())) startPoll(); else if (tab === 'dreamer') stopPoll();
    } catch (e) { S.cache[tab] = { error: e.message }; }
    render();
  }
  function startPoll() {
    if (S.poll) return;
    S.poll = setInterval(async () => {
      if (!isOpen() || S.view !== 'project') return stopPoll();
      try {
        const d = await get('/projects/dreamer', { id: S.pid });
        S.cache.dreamer = d;
        const at = d.last && d.last.at;
        // the spawned run takes a moment to start: keep waiting until it is seen running or a new run lands
        if (!d.running && (!starting() || (at && at !== S.dreamFrom))) {
          const was = S.dreamFrom !== undefined; S.dreamAt = 0; S.dreamFrom = undefined;
          stopPoll(); if (was || at) toast('Dreamer finished'); loadProject(S.pid, true);
        } else if (d.running) S.dreamAt = 0;
        render();
      } catch (_) {}
    }, 3000);
  }
  const starting = () => S.dreamAt && Date.now() - S.dreamAt < 30000;
  function stopPoll() { if (S.poll) { clearInterval(S.poll); S.poll = null; } }

  async function act(fn, ok) {
    if (S.busy) return;
    S.busy = true; render();
    try { const out = await fn(); if (ok) toast(typeof ok === 'function' ? ok(out) : ok); return out; }
    catch (e) {
      toast(e.message, 'error');
      if (e.conflict && S.pid) loadProject(S.pid, true);
    } finally { S.busy = false; render(); badgeSoon(); }
  }

  // WebUI native project bound to the workspace: sidebar grouping + chats that start in it
  async function ensureWebuiProject(p) {
    const ws = p.workspace;
    let wid = p.webui_project_id;
    if (wid) {
      try { const all = await webui('/api/projects'); if (!(all.projects || []).some((x) => x.project_id === wid)) wid = null; }
      catch (_) {}
    }
    if (!wid) {
      const made = await webui('/api/projects/create', { name: p.name });
      wid = made.project && made.project.project_id;
      const up = await call('/projects/update', { id: p.id, hash: p.hash, webui_project_id: wid });
      p.hash = up.hash; p.webui_project_id = wid;
    }
    await webui('/api/projects/bind', { project_id: wid, workspaces: [ws], default_workspace: ws });
    return wid;
  }
  async function newTask(p) {
    await act(async () => {
      if (!p.folders.length) throw new Error('Add a folder first, so Hermes knows where the work happens.');
      const wid = await ensureWebuiProject(p);
      close();
      if (typeof newSession === 'function') await newSession(false, { project_id: wid, workspace: p.workspace, worktree: false });
      try { if (typeof renderSessionList === 'function') renderSessionList({ deferWhileInteracting: false }); } catch (_) {}
      setTimeout(() => { const ta = document.querySelector('#msg, textarea#message, .composer textarea'); if (ta) ta.focus(); }, 300);
    });
  }

  // ── render ──
  function render() {
    if (!root || !isOpen()) return;
    const keepScroll = root.scrollTop;
    // re-rendering the same open sheet (a tick, a poll) must not replay its open animation or lose its scroll
    const same = S.sheet && S.sheet === S.shown;
    const view = S.sheet ? `${S.sheet.mode || ''}|${(S.sheet.data && S.sheet.data.path) || ''}` : '';  // a new folder starts at the top
    const old = same ? root.querySelector('.hxp-sheet') : null;
    const scrolls = old ? [old.scrollTop, ...[...old.querySelectorAll('.hxp-scroll')].map((x) => x.scrollTop)] : null;
    root.replaceChildren(S.view === 'project' ? viewProject() : viewList());
    if (S.sheet) {
      const sc = sheet(S.sheet);
      if (same || S.sheet.steady) sc.classList.add('is-steady');
      S.sheet.steady = false;
      root.append(sc);
      if (scrolls) {
        const box = sc.querySelector('.hxp-sheet');
        box.scrollTop = scrolls[0];
        if (view === S.shownView) [...box.querySelectorAll('.hxp-scroll')].forEach((x, i) => { if (scrolls[i + 1] != null) x.scrollTop = scrolls[i + 1]; });
      }
    }
    S.shown = S.sheet; S.shownView = view;
    root.scrollTop = keepScroll;
  }

  function top(title, sub, left, right) {
    return h('header', { class: 'hxp-top' },
      left || btn('hx-back', 'Back', close, 'ArrowLeft01Icon', { 'aria-label': 'Back to chats' }),
      h('div', { class: 'hxp-title' }, h('span', { text: title }), sub ? h('em', { text: sub }) : null),
      ...(right || []));
  }

  function viewList() {
    const wrap = h('div', { class: 'hxp-page' });
    wrap.append(top('Projects', S.list ? plural(S.list.length, 'project') : '', null,
      [btn('hxp-primary hxp-sm', 'New project', newProject, 'Add01Icon')]));
    const main = h('main', { class: 'hxp-main' });
    if (S.err) main.append(h('p', { class: 'hxp-err', text: S.err }));
    if (!S.list && !S.err) main.append(h('p', { class: 'hxp-muted', text: 'Loading…' }));
    if (S.list && !S.list.length) {
      main.append(h('div', { class: 'hxp-empty' },
        h('div', { class: 'hxp-empty-i', icon: 'Briefcase01Icon', size: 28 }),
        h('h2', { text: 'One project, all its folders' }),
        h('p', { text: 'Group the repos that make up one product (site, app, API…). Hermes then works across them as one system, and learns from every session in that project.' }),
        btn('hxp-primary', 'Create your first project', newProject, 'Add01Icon')));
    }
    if (S.list && S.list.length) {
      const grid = h('div', { class: 'hxp-grid' });
      for (const p of S.list) {
        const s = p.summary || {};
        grid.append(h('button', { type: 'button', class: `hxp-card hxp-pcard${p.status !== 'active' ? ' is-arch' : ''}`, onclick: () => { S.tab = 'folders'; loadProject(p.id); } },
          h('div', { class: 'hxp-pcard-h' }, h('strong', { text: p.name }), p.status !== 'active' ? h('span', { class: 'hxp-tag', text: 'archived' }) : null,
            s.pending ? h('span', { class: 'hxp-badge', text: `${s.pending} to review` }) : null),
          p.description ? h('p', { class: 'hxp-pdesc', text: p.description }) : null,
          h('div', { class: 'hxp-meta' }, h('span', { text: plural(s.folders || 0, 'folder') }), h('span', { text: plural(s.memories || 0, 'memory') }),
            h('span', { text: `Dreamed ${ago(p.last_dream)}` })),
          h('div', { class: 'hxp-chips' }, ...(p.folders || []).slice(0, 4).map((f) => h('span', { class: 'hxp-chip-s', text: f.label })),
            (p.folders || []).length > 4 ? h('span', { class: 'hxp-chip-s', text: `+${p.folders.length - 4}` }) : null)));
      }
      main.append(grid);
    }
    main.append(h('label', { class: 'hxp-toggle' }, h('input', { type: 'checkbox', checked: S.archived || null, onchange: (e) => { S.archived = e.target.checked; loadList(); } }), h('span', { text: 'Show archived' })));
    wrap.append(main);
    return wrap;
  }

  const TABS = [['folders', 'Folders', 'Folder01Icon'], ['motion', 'Motion', 'Video01Icon'], ['sessions', 'Sessions', 'Clock01Icon'], ['memory', 'Memory', 'Brain01Icon'],
                ['dreamer', 'Dreamer', 'Moon02Icon'], ['skills', 'Skills', 'SparklesIcon'], ['instructions', 'Instructions', 'FileTextIcon'],
                ['architecture', 'Architecture', 'Layers01Icon'], ['settings', 'Settings', 'Settings02Icon']];

  function viewProject() {
    const p = S.p;
    const wrap = h('div', { class: 'hxp-page' });
    const back = btn('hx-back', 'Projects', toList, 'ArrowLeft01Icon', { 'aria-label': 'Back to all projects' });
    if (!p) {
      wrap.append(top('Project', '', back), h('main', { class: 'hxp-main' }, h('p', { class: S.err ? 'hxp-err' : 'hxp-muted', text: S.err || 'Loading…' })));
      return wrap;
    }
    const s = p.summary || {};
    const prim = p.folders.find((f) => f.id === p.primary_folder);
    const dreamRunning = (S.cache.dreamer && S.cache.dreamer.running) || starting();
    wrap.append(top(p.name, p.status !== 'active' ? 'archived' : '', back));
    const main = h('main', { class: 'hxp-main' });
    if (S.err) main.append(h('p', { class: 'hxp-err', text: S.err }));
    // hero: the useful state at a glance
    main.append(h('section', { class: 'hxp-hero' },
      p.description ? h('p', { class: 'hxp-pdesc', text: p.description }) : null,
      h('div', { class: 'hxp-stats' },
        stat(s.folders, `linked ${word(s.folders, 'folder')}`, () => tab('folders')),
        stat(s.recent_sessions, `${word(s.recent_sessions, 'session')} (30 d)`, () => tab('sessions')),
        stat(s.pending, 'to review', () => tab('dreamer'), s.pending ? 'is-hot' : ''),
        stat(s.skills_proposed, `${word(s.skills_proposed, 'skill')} proposed`, () => tab('skills')),
        stat(s.memories, word(s.memories, 'memory'), () => tab('memory'))),
      h('div', { class: 'hxp-meta' },
        h('span', { text: prim ? `Primary: ${prim.label}` : 'No folders yet' }),
        h('span', { text: `Last dream: ${ago(s.last_dream)}` })),
      h('div', { class: 'hxp-row' },
        btn('hxp-primary', 'New task', () => newTask(p), 'BubbleChatIcon', { disabled: p.status !== 'active' || S.busy || null }),
        btn('hxp-ghost', dreamRunning ? 'Dreaming…' : 'Run Dreamer', () => runDream(p), 'Moon02Icon', { disabled: dreamRunning || p.status !== 'active' || S.busy || null }))));
    // tabs
    const tabs = h('nav', { class: 'hxp-tabs', role: 'tablist' });
    for (const [id, label, ico] of TABS) {
      const n = id === 'dreamer' && s.pending ? s.pending : null;
      tabs.append(h('button', { type: 'button', role: 'tab', 'aria-selected': String(S.tab === id), class: `hxp-tab${S.tab === id ? ' is-on' : ''}`, onclick: () => tab(id) },
        h('span', { class: 'hxp-i', icon: ico, size: 15 }), h('span', { text: label }), n ? h('b', { text: String(n) }) : null));
    }
    main.append(tabs);
    // The tab bar scrolls sideways when the tabs don't fit: fade its right edge until the end is reached,
    // and keep the open tab in view.
    const edge = () => { tabs.classList.toggle('is-over', tabs.scrollWidth > tabs.clientWidth + 1);
      tabs.classList.toggle('is-end', tabs.scrollLeft + tabs.clientWidth >= tabs.scrollWidth - 2); };
    tabs.addEventListener('scroll', edge, { passive: true });
    requestAnimationFrame(() => { const on = tabs.querySelector('.is-on'); if (on && tabs.scrollWidth > tabs.clientWidth) on.scrollIntoView({ block: 'nearest', inline: 'nearest' }); edge(); });
    setTimeout(edge, 60);
    const body = h('section', { class: 'hxp-body' });
    const d = S.cache[S.tab];
    if (d && d.error) body.append(h('p', { class: 'hxp-err', text: d.error }));
    else body.append(({ folders: tabFolders, motion: tabMotion, sessions: tabSessions, memory: tabMemory, dreamer: tabDreamer, skills: tabSkills,
                        instructions: tabInstructions, architecture: tabArchitecture, settings: tabSettings })[S.tab](p, d));
    main.append(body);
    wrap.append(main);
    return wrap;
  }
  function stat(n, label, on, cls) {
    return h('button', { type: 'button', class: `hxp-stat ${cls || ''}`, onclick: on }, h('strong', { text: String(n == null ? '–' : n) }), h('span', { text: label }));
  }
  function tab(id) { S.tab = id; addr(); render(); loadTab(); }
  const loading = () => h('p', { class: 'hxp-muted', text: 'Loading…' });

  async function runDream(p) {
    await act(async () => {
      const r = await call('/projects/dream', { id: p.id });
      if (!r.started) throw new Error(r.why || 'Dreamer did not start');
      S.dreamAt = Date.now();
      S.dreamFrom = (S.cache.dreamer && S.cache.dreamer.last && S.cache.dreamer.last.at) || null;
      S.tab = 'dreamer'; addr();
      startPoll();
      loadTab(true);
    }, 'Dreamer started. It reads this project’s sessions in the background.');
  }

  // ── tabs ──
  function tabFolders(p) {
    const out = h('div');
    const list = h('div', { class: 'hxp-list' });
    for (const f of p.folders) {
      const live = f.live || {};
      const isPrim = f.id === p.primary_folder;
      list.append(h('div', { class: `hxp-item${f.missing ? ' is-missing' : ''}` },
        h('span', { class: 'hxp-item-i', icon: f.git ? 'GitBranchIcon' : 'Folder01Icon', size: 18 }),
        h('div', { class: 'hxp-item-b' },
          h('div', { class: 'hxp-item-t' }, h('strong', { text: f.label }), isPrim ? h('span', { class: 'hxp-tag is-ink', text: 'primary' }) : null,
            f.missing ? h('span', { class: 'hxp-tag is-bad', text: 'missing on this computer' }) : null),
          h('div', { class: 'hxp-item-s', text: f.display }),
          h('div', { class: 'hxp-meta' },
            f.git ? h('span', { text: [live.branch, live.head, live.dirty ? `${live.dirty} uncommitted` : 'clean'].filter(Boolean).join(' · ') }) : h('span', { text: 'not a git repo' }),
            f.git && f.git.remote ? h('span', { text: f.git.remote }) : null,
            (f.context_files || []).length ? h('span', { text: `context: ${f.context_files.slice(0, 3).join(', ')}${f.context_files.length > 3 ? '…' : ''}` }) : null)),
        h('div', { class: 'hxp-item-a' },
          !isPrim ? btn('hxp-ghost hxp-sm', 'Make primary', () => act(() => call('/projects/folder/primary', { id: p.id, hash: p.hash, folder_id: f.id }).then(() => loadProject(p.id, true)), 'Primary folder changed')) : null,
          btn('hxp-ib', '', () => { S.sheet = { kind: 'confirm', title: `Remove ${f.label}?`, text: 'The folder stays on your computer. Hermes stops treating it as part of this project; its past sessions and memories stay with the project.', ok: 'Remove', danger: true, run: () => call('/projects/folder/remove', { id: p.id, hash: p.hash, folder_id: f.id }).then(() => loadProject(p.id, true)) }; render(); }, 'Delete02Icon', { 'aria-label': `Remove ${f.label}`, title: 'Remove from project' }))));
    }
    if (!p.folders.length) list.append(h('p', { class: 'hxp-muted', text: 'No folders yet. Add the repos and folders that make up this project.' }));
    out.append(h('div', { class: 'hxp-row hxp-row-end' },
      btn('hxp-ghost hxp-sm', 'Refresh', () => act(() => call('/projects/folder/refresh', { id: p.id }).then(() => loadProject(p.id, true)), 'Folder details refreshed'), 'RefreshIcon'),
      btn('hxp-primary hxp-sm', 'Add folder', () => openPicker({ forProject: p }), 'FolderAddIcon', { disabled: p.status !== 'active' || null })), list);
    for (const c of p.legacy_candidates || []) {
      out.append(h('div', { class: 'hxp-card hxp-note' },
        h('div', {}, h('strong', { text: `Older memory found for “${c.key}”` }),
          h('p', { class: 'hxp-muted', text: `${plural(c.memories, 'note')} saved before Projects existed (from ${c.folder}). Include them in this project’s memory? Nothing is moved; you can undo this in Settings.` })),
        btn('hxp-ghost hxp-sm', 'Include', () => act(() => call('/projects/update', { id: p.id, hash: p.hash, memory_keys: [...(p.memory_keys || []), c.key] }).then(() => loadProject(p.id, true)), 'Included'))));
    }
    out.append(h('p', { class: 'hxp-foot', text: `Workers run in the primary folder and can open the others. Each folder’s own CLAUDE.md and .hermes/ notes stay with that repo.` }));
    return out;
  }

  // Motion films made for this project: they read its folders (read only) and reuse its saved colours
  function tabMotion(p) {
    const st = window.hxStudio;
    const out = h('div', { class: 'hxp-mo' });
    if (!st || !st.filmsFor) { out.append(h('p', { class: 'hxp-muted', text: 'The motion studio isn’t loaded.' })); return out; }
    const open = (o) => { close(); st.open({ push: true, ...o }); };
    out.append(h('div', { class: 'hxp-mo-h' },
      h('p', { class: 'hxp-muted', text: p.folders.length ? 'Films for this project are grounded in its folders: the product, its copy and its brand. Colour profiles you save in a film are kept here for the next one.' : 'Add a folder first: a film reads the project’s product, copy and brand from it.' }),
      btn('hxp-primary', 'New film', () => open({ home: true, forProject: p.id }), 'Add01Icon', { disabled: p.status !== 'active' || null })));
    const cols = p.colours || [];
    if (cols.length) {
      out.append(h('div', { class: 'hxp-mo-cols' }, h('b', { text: 'Saved colours' }),
        h('div', { class: 'hxp-mo-cl' }, ...cols.map((c) => h('span', { class: 'hxp-mo-c', title: Object.entries(c.colors).map(([k, v]) => `${k} ${v}`).join(' · ') },
          h('i', null, ...Object.values(c.colors).slice(0, 5).map((v) => { const e = h('em'); e.style.background = /^#[0-9A-F]{6}$/i.test(v) ? v : 'transparent'; return e; })),
          h('span', { text: c.name }))))));
    }
    const list = h('div', { class: 'hxp-list' }, h('p', { class: 'hxp-muted', text: 'Loading…' }));
    out.append(list);
    st.filmsFor(p.id).then((films) => {
      list.textContent = '';
      if (!films.length) { list.append(h('p', { class: 'hxp-muted', text: 'No films for this project yet.' })); return; }
      for (const f of films) {
        list.append(h('button', { type: 'button', class: 'hxp-item hxp-click', onclick: () => open({ sid: f.sid }) },
          h('span', { class: 'hxp-i', icon: 'Video01Icon', size: 18 }),
          h('div', { class: 'hxp-item-b' }, h('div', { class: 'hxp-item-t' }, h('span', { class: 'hxp-clip', text: f.title })),
            h('div', { class: 'hxp-meta' }, h('span', { text: f.step || 'audio' }), f.at ? h('span', { text: ago(new Date(f.at).toISOString()) }) : null)),
          h('span', { class: 'hxp-i', icon: 'ArrowRight01Icon', size: 16 })));
      }
    }).catch(() => { list.textContent = ''; list.append(h('p', { class: 'hxp-err', text: 'Couldn’t list the films.' })); });
    return out;
  }

  function tabSessions(p, d) {
    if (!d) return loading();
    if (!d.sessions.length) return h('p', { class: 'hxp-muted', text: 'No sessions yet. Start a task from this project and it will show up here.' });
    const list = h('div', { class: 'hxp-list' });
    for (const s of d.sessions) {
      list.append(h('button', { type: 'button', class: 'hxp-item hxp-click', onclick: () => openSession(p, s) },
        h('span', { class: `hxp-dot is-${s.outcome || 'unknown'}`, title: s.outcome || 'unknown' }),
        h('div', { class: 'hxp-item-b' },
          h('div', { class: 'hxp-item-t' }, h('span', { class: 'hxp-clip', text: s.request || '(no request text)' })),
          h('div', { class: 'hxp-meta' }, h('span', { text: ago(s.started) }), s.folders.length ? h('span', { text: s.folders.join(' + ') }) : null,
            s.duration_s ? h('span', { text: `${Math.round(s.duration_s / 60) || 1} min` }) : null,
            s.files_modified ? h('span', { text: plural(s.files_modified, 'file') + ' changed' }) : null,
            s.errors ? h('span', { class: 'is-bad', text: plural(s.errors, 'error') }) : null)),
        h('span', { class: 'hxp-i', icon: 'ArrowRight01Icon', size: 16 })));
    }
    return list;
  }
  async function openSession(p, s) {
    S.sheet = { kind: 'session', row: s, data: null }; render();
    try { S.sheet.data = await get('/projects/session', { id: p.id, trace: s.id }); } catch (e) { S.sheet.error = e.message; }
    render();
  }

  function tabMemory(p, d) {
    if (!d) return loading();
    const out = h('div');
    const q = h('input', { type: 'search', placeholder: 'Search this project’s memory', class: 'hxp-search', value: S.memQ || '' });
    q.addEventListener('keydown', async (e) => {
      if (e.key !== 'Enter') return;
      S.memQ = q.value.trim();
      if (!S.memQ) { S.memHits = null; return render(); }
      try { S.memHits = (await get('/projects/search', { id: p.id, q: S.memQ })).hits; } catch (err) { toast(err.message, 'error'); }
      render();
    });
    out.append(q);
    const rows = S.memHits && S.memQ ? S.memHits : d.memories;
    if (!rows.length) out.append(h('p', { class: 'hxp-muted', text: S.memQ ? 'No matches in this project or global memory.' : 'No project memory yet. Approved Dreamer proposals and lessons you ask Hermes to remember land here.' }));
    const groups = {};
    for (const m of rows) (groups[m.type || 'fact'] = groups[m.type || 'fact'] || []).push(m);
    for (const [type, ms] of Object.entries(groups)) {
      out.append(h('h3', { class: 'hxp-h3', text: many(`${type[0].toUpperCase()}${type.slice(1)}`) }));
      const list = h('div', { class: 'hxp-list' });
      for (const m of ms) list.append(memRow(p, m));
      out.append(list);
    }
    out.append(h('p', { class: 'hxp-foot', text: 'Memory helps agents; the code in the repos is the source of truth. Every change is versioned and can be rolled back.' }));
    return out;
  }
  const ownMem = (p, m) => m.status === 'active' && /^projects\//.test(m.id) && [p.id, ...(p.memory_keys || [])].includes(m.id.split('/')[1]);
  function deleteMemory(p, m) {
    S.sheet = { kind: 'confirm', title: `Delete “${m.title || m.id}”?`, danger: true, ok: 'Delete',
      text: 'Agents stop seeing it straight away. It is kept in the memory history on the computer, so it can be restored.',
      run: () => call('/projects/memory/delete', { id: p.id, mem: m.id }).then(() => { S.cache = {}; S.memHits = null; return loadProject(p.id, true); }),
      done: 'Memory deleted' };
    render();
  }
  function memRow(p, m) {
    return h('div', { class: 'hxp-item' },
      h('button', { type: 'button', class: 'hxp-item-link', onclick: () => openMemory(p, m) },
      h('span', { class: 'hxp-item-i', icon: m.mirror ? 'Copy01Icon' : 'Note01Icon', size: 17 }),
      h('div', { class: 'hxp-item-b' },
        h('div', { class: 'hxp-item-t' }, h('strong', { class: 'hxp-clip', text: m.title || m.id }),
          m.status !== 'active' ? h('span', { class: 'hxp-tag', text: m.status }) : null,
          m.trust === 'external' ? h('span', { class: 'hxp-tag is-bad', text: 'unverified source' }) : null,
          m.quarantined_until ? h('span', { class: 'hxp-tag', text: 'new: shown to agents tomorrow' }) : null,
          m.trusted === false ? h('span', { class: 'hxp-tag is-bad', text: 'edited outside Hermes' }) : null),
        m.description && m.description !== m.title ? h('div', { class: 'hxp-item-s hxp-clip', text: m.description }) : null)),
      ownMem(p, m) ? btn('hxp-ib', '', () => deleteMemory(p, m), 'Delete02Icon', { 'aria-label': `Delete ${m.title || m.id}`, title: 'Delete memory' }) : null);
  }
  async function openMemory(p, m) {
    S.sheet = { kind: 'memory', row: m, data: null }; render();
    try { S.sheet.data = await get('/projects/memory/read', { id: p.id, mem: m.id }); } catch (e) { S.sheet.error = e.message; }
    render();
  }

  function tabDreamer(p, d) {
    if (!d) return loading();
    const out = h('div');
    const c = d.counts || {};
    out.append(h('div', { class: 'hxp-stats hxp-stats-sm' },
      stat(d.last ? ago(d.last.at) : 'never', 'last dream'), stat(d.last ? d.last.traces_analysed : 0, 'sessions analysed'),
      stat(c.patterns, 'patterns found'), stat(c.pending, 'pending', null, c.pending ? 'is-hot' : ''), stat(c.approved, 'approved'),
      stat(c.rejected, 'rejected'), stat(c.recurring_failures, 'recurring failures'), stat(c.skills_proposed, 'skills proposed'),
      stat(c.memory_conflicts, 'memory conflicts'), stat(c.stale_memories, 'stale memories')));
    if (d.running) out.append(h('div', { class: 'hxp-job' }, h('span', { class: 'hxp-spin', icon: 'Loading03Icon', size: 16 }), h('span', { text: 'Dreaming over this project’s sessions…' })));
    out.append(h('h3', { class: 'hxp-h3', text: 'Pending proposals' }));
    if (!d.pending.length) out.append(h('p', { class: 'hxp-muted', text: 'Nothing waiting. When the Dreamer finds a lesson backed by several sessions, it lands here for you to approve.' }));
    const pl = h('div', { class: 'hxp-list' });
    for (const x of d.pending) pl.append(propRow(p, x));
    out.append(pl);
    const fs = d.findings || [];
    if (fs.length) {
      out.append(h('h3', { class: 'hxp-h3', text: 'Findings from the last dream' }));
      const fl = h('div', { class: 'hxp-list' });
      for (const f of fs.slice(0, 30)) {
        fl.append(h('button', { type: 'button', class: 'hxp-item hxp-click', onclick: () => { S.sheet = { kind: 'finding', f }; render(); } },
          h('span', { class: 'hxp-item-i', icon: SPEC_ICON[f.specialist] || 'SparklesIcon', size: 17 }),
          h('div', { class: 'hxp-item-b' },
            h('div', { class: 'hxp-item-t' }, h('span', { class: 'hxp-clip', text: f.title }), f.report_only ? h('span', { class: 'hxp-tag', text: 'for review' }) : null),
            h('div', { class: 'hxp-meta' }, h('span', { text: SPEC_NAME[f.specialist] || f.specialist }),
              f.conversations ? h('span', { text: `${f.affected_sessions || f.conversations} of ${f.relevant_sessions || '?'} ${f.relevant_sessions === 1 ? 'session' : 'sessions'}` }) : null,
              f.confidence ? h('span', { text: `confidence ${Math.round(f.confidence * 100)}%` }) : null,
              f.folders.length ? h('span', { text: f.folders.join(' + ') }) : null)),
          h('span', { class: 'hxp-i', icon: 'ArrowRight01Icon', size: 16 })));
      }
      out.append(fl);
    }
    if (d.decided.length) {
      out.append(h('h3', { class: 'hxp-h3', text: 'Decided' }));
      const dl = h('div', { class: 'hxp-list' });
      for (const x of d.decided.slice(0, 15)) dl.append(propRow(p, x));
      out.append(dl);
    }
    if (d.runs.length) {
      out.append(h('h3', { class: 'hxp-h3', text: 'Runs' }));
      const rl = h('div', { class: 'hxp-list' });
      for (const r of d.runs) rl.append(h('button', { type: 'button', class: 'hxp-item hxp-click', onclick: () => openRun(p, r) },
        h('span', { class: 'hxp-item-i', icon: 'Moon02Icon', size: 17 }),
        h('div', { class: 'hxp-item-b' }, h('div', { class: 'hxp-item-t', text: `${new Date(r.at).toLocaleString()} · ${r.trigger}` }),
          h('div', { class: 'hxp-meta' }, h('span', { text: plural(r.sessions || 0, 'session') }), h('span', { text: plural(r.findings, 'finding') }), h('span', { text: plural(r.proposals, 'proposal') }))),
        h('span', { class: 'hxp-i', icon: 'ArrowRight01Icon', size: 16 })));
      out.append(rl);
    }
    out.append(h('p', { class: 'hxp-foot', text: 'The Dreamer runs after enough new sessions, nightly, or when you ask. It never changes memory by itself: everything it learns comes here first.' }));
    return out;
  }
  const SPEC_NAME = { failures: 'Repeated failure', feedback: 'Your feedback', hygiene: 'Memory quality', security: 'Security', tools: 'Tool failures',
                      rediscovery: 'Documentation gap', workflows: 'Workflow / skill candidate', cross_repo: 'Across repos', conflicts: 'Memory vs code' };
  const SPEC_ICON = { failures: 'Alert02Icon', feedback: 'BubbleChatIcon', hygiene: 'Brain01Icon', security: 'LockPasswordIcon', tools: 'Wrench01Icon',
                      rediscovery: 'Search01Icon', workflows: 'Rocket01Icon', cross_repo: 'Layers01Icon', conflicts: 'CodeIcon' };
  function propRow(p, x) {
    return h('button', { type: 'button', class: 'hxp-item hxp-click', onclick: () => openProposal(p, x) },
      h('span', { class: 'hxp-item-i', icon: x.type === 'skill' ? 'SparklesIcon' : x.type === 'deprecate' ? 'Archive02Icon' : 'Note01Icon', size: 17 }),
      h('div', { class: 'hxp-item-b' },
        h('div', { class: 'hxp-item-t' }, h('span', { class: 'hxp-clip', text: x.title }),
          x.status !== 'pending' ? h('span', { class: `hxp-tag ${x.status === 'approved' ? 'is-ok' : ''}`, text: x.status }) : null,
          x.flags.length && x.status === 'pending' ? h('span', { class: 'hxp-tag is-bad', text: 'check carefully' }) : null),
        h('div', { class: 'hxp-meta' }, h('span', { text: { create: 'new memory', modify: 'correction', deprecate: 'retire memory', merge: 'merge', skill: 'new skill' }[x.type] || x.type }),
          h('span', { text: plural(x.sessions, 'session') }), x.confidence ? h('span', { text: `confidence ${Math.round(x.confidence * 100)}%` }) : null,
          h('span', { text: ago(x.decided_at || x.created_at) }))),
      h('span', { class: 'hxp-i', icon: 'ArrowRight01Icon', size: 16 }));
  }
  async function openProposal(p, x) {
    S.sheet = { kind: 'proposal', row: x, data: null, confirm: false }; render();
    try { S.sheet.data = await get('/projects/proposal', { id: p.id, pid: x.id }); } catch (e) { S.sheet.error = e.message; }
    render();
  }
  async function openRun(p, r) {
    S.sheet = { kind: 'run', row: r, data: null }; render();
    try { S.sheet.data = await get('/projects/dreamer/run', { id: p.id, cycle: r.cycle }); } catch (e) { S.sheet.error = e.message; }
    render();
  }

  function tabSkills(p, d) {
    if (!d) return loading();
    const out = h('div');
    out.append(h('h3', { class: 'hxp-h3', text: 'Project skills' }));
    if (!d.skills.length) out.append(h('p', { class: 'hxp-muted', text: 'None yet. When agents repeat the same successful procedure in this project, the Dreamer can propose it as a skill. Approved skills are offered only to this project’s agents.' }));
    const l = h('div', { class: 'hxp-list' });
    for (const s of d.skills) l.append(h('div', { class: 'hxp-item' }, h('span', { class: 'hxp-item-i', icon: 'SparklesIcon', size: 17 }),
      h('div', { class: 'hxp-item-b' }, h('strong', { text: s.name }), h('div', { class: 'hxp-item-s', text: s.description }))));
    out.append(l);
    if (d.proposed.length) {
      out.append(h('h3', { class: 'hxp-h3', text: 'Proposed' }));
      const pl = h('div', { class: 'hxp-list' });
      for (const x of d.proposed) pl.append(propRow(p, x));
      out.append(pl);
    }
    return out;
  }

  function tabInstructions(p) {
    const ta = h('textarea', { class: 'hxp-ta', rows: '14', placeholder: 'What every agent working on this project should know: conventions, things never to do, how to run and test it, who it is for…', value: p.instructions || '' });
    const count = h('span', { class: 'hxp-muted', text: `${(p.instructions || '').length} / 16000` });
    ta.addEventListener('input', () => { count.textContent = `${ta.value.length} / 16000`; });
    return h('div', {},
      h('p', { class: 'hxp-muted', text: 'Written by you, given to every agent that works on this project (short ones inline, long ones as a file they read first).' }),
      ta,
      h('div', { class: 'hxp-row hxp-row-end' }, count,
        btn('hxp-primary hxp-sm', 'Save', () => act(() => call('/projects/update', { id: p.id, hash: p.hash, instructions: ta.value }).then((x) => { S.p = Object.assign(S.p, x); }), 'Instructions saved'), 'FloppyDiskIcon', { disabled: S.busy || null })));
  }

  function tabArchitecture(p, d) {
    if (!d) return loading();
    const out = h('div');
    out.append(h('h3', { class: 'hxp-h3', text: 'Decisions & architecture (project memory)' }));
    if (!d.memories.length) out.append(h('p', { class: 'hxp-muted', text: 'No architecture or decision notes yet.' }));
    const l = h('div', { class: 'hxp-list' });
    for (const m of d.memories) l.append(memRow(p, m));
    out.append(l);
    out.append(h('h3', { class: 'hxp-h3', text: 'In the repos' }));
    for (const r of d.repo) {
      const rl = h('div', { class: 'hxp-list' });
      if (!r.files.length) rl.append(h('p', { class: 'hxp-muted', text: `${r.folder}: no CLAUDE.md or .hermes/ notes` }));
      for (const f of r.files) rl.append(h('button', { type: 'button', class: 'hxp-item hxp-click', onclick: () => openFile(p, r, f) },
        h('span', { class: 'hxp-item-i', icon: 'FileTextIcon', size: 17 }),
        h('div', { class: 'hxp-item-b' }, h('strong', { text: f }), h('div', { class: 'hxp-item-s', text: r.folder })),
        h('span', { class: 'hxp-i', icon: 'ArrowRight01Icon', size: 16 })));
      out.append(rl);
    }
    return out;
  }
  async function openFile(p, r, f) {
    S.sheet = { kind: 'file', title: `${r.folder} / ${f}`, data: null }; render();
    try { S.sheet.data = await get('/projects/file', { id: p.id, folder: r.folder_id, path: f }); } catch (e) { S.sheet.error = e.message; }
    render();
  }

  function tabSettings(p) {
    const name = h('input', { value: p.name, maxlength: '80' });
    const desc = h('textarea', { rows: '3', value: p.description || '', maxlength: '600' });
    const after = h('input', { type: 'number', min: '3', max: '500', value: String((p.dreamer || {}).after_sessions || 10) });
    const out = h('div', { class: 'hxp-form' },
      h('label', {}, h('span', { text: 'Name' }), name),
      h('label', {}, h('span', { text: 'Description' }), desc),
      h('label', {}, h('span', { text: 'Dream after this many new sessions' }), after),
      h('div', { class: 'hxp-row hxp-row-end' }, btn('hxp-primary hxp-sm', 'Save', () => act(async () => {
        const x = await call('/projects/update', { id: p.id, hash: p.hash, name: name.value, description: desc.value, dreamer: { after_sessions: Number(after.value) } });
        S.p = Object.assign(S.p, x);
        if (p.webui_project_id) webui('/api/projects/rename', { project_id: p.webui_project_id, name: x.name }).catch(() => {});
      }, 'Saved'), 'FloppyDiskIcon')));
    if ((p.memory_keys || []).length) {
      out.append(h('h3', { class: 'hxp-h3', text: 'Included older memory' }));
      for (const k of p.memory_keys) out.append(h('div', { class: 'hxp-row' }, h('span', { text: k }),
        btn('hxp-ghost hxp-sm', 'Stop including', () => act(() => call('/projects/update', { id: p.id, hash: p.hash, memory_keys: p.memory_keys.filter((x) => x !== k) }).then(() => loadProject(p.id, true)), 'Updated'))));
    }
    out.append(h('p', { class: 'hxp-muted', text: 'Auto-approval of Dreamer proposals is off. It can only be turned on in Hermes’ config on the computer, and never covers global memory or skills.' }));
    out.append(h('h3', { class: 'hxp-h3', text: 'Project' }));
    out.append(h('p', { class: 'hxp-muted', text: `id ${p.id} · workspace ${p.workspace.replace(/^(\/Users\/[^/]+|[A-Za-z]:\\Users\\[^\\]+)/, '~')}` }));
    const actions = h('div', { class: 'hxp-row' });
    if (p.status === 'active') actions.append(btn('hxp-ghost hxp-sm', 'Archive', () => { S.sheet = { kind: 'confirm', title: `Archive ${p.name}?`, text: 'Hermes stops routing work to it and its folders become free to use elsewhere. Memory and sessions are kept; you can unarchive any time.', ok: 'Archive', run: () => call('/projects/status', { id: p.id, hash: p.hash, status: 'archived' }).then(() => loadProject(p.id, true)) }; render(); }, 'Archive02Icon'));
    else {
      actions.append(btn('hxp-ghost hxp-sm', 'Unarchive', () => act(() => call('/projects/status', { id: p.id, hash: p.hash, status: 'active' }).then(() => loadProject(p.id, true)), 'Unarchived'), 'RotateLeft01Icon'));
      actions.append(btn('hxp-ghost hxp-sm is-danger', 'Delete…', () => { S.sheet = { kind: 'delete', p }; render(); }, 'Delete02Icon'));
    }
    out.append(actions);
    return out;
  }

  // ── phone: pull a sheet down to dismiss it (the model picker's drag, copied). Starts only when the
  // sheet (and the inner list under the finger) is scrolled to the top, never from a field, and only on
  // a mostly vertical pull. Each render builds a new sheet element, so listeners never pile up.
  const reduced = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  function swipeDown(panel, onClose) {
    let y0 = null, x0 = 0, dy = 0, live = false;
    const reset = () => { panel.style.transition = ''; panel.style.transform = ''; const s = panel.parentElement; if (s) s.style.opacity = ''; };
    panel.addEventListener('touchstart', (e) => {
      y0 = null; live = false; dy = 0;
      if (!phone() || e.touches.length !== 1) return;
      const t = e.target;
      if (t.closest && t.closest('input, textarea, select, [contenteditable], .hxp-chips')) return;
      if (panel.scrollTop > 0) return;
      for (let n = t; n && n !== panel; n = n.parentElement) if (n.scrollTop > 0) return;  // an inner list that's scrolled
      y0 = e.touches[0].clientY; x0 = e.touches[0].clientX;
    }, { passive: true });
    panel.addEventListener('touchmove', (e) => {
      if (y0 == null) return;
      const t = e.touches[0];
      dy = Math.max(0, t.clientY - y0);
      if (!live) {
        if (Math.abs(t.clientX - x0) > 12 && Math.abs(t.clientX - x0) > dy) { y0 = null; return; }  // sideways: not a dismiss
        if (dy < 8) return;
        if (panel.scrollTop > 0) { y0 = null; return; }
        live = true;
      }
      if (reduced()) return;
      panel.style.transition = 'none';
      panel.style.transform = `translateY(${dy}px)`;
      const s = panel.parentElement; if (s) s.style.opacity = String(Math.max(0.35, 1 - dy / 600));
    }, { passive: true });
    const end = () => {
      if (y0 == null) return;
      y0 = null;
      const go = live && dy > 90;
      live = false;
      if (!go) { reset(); return; }
      if (reduced()) { onClose(); return; }
      panel.style.transition = 'transform .2s var(--hx-ease, ease)';
      panel.style.transform = 'translateY(100%)';
      setTimeout(onClose, 180);
    };
    panel.addEventListener('touchend', end);
    panel.addEventListener('touchcancel', () => { y0 = null; live = false; reset(); });
  }

  // ── sheets ──
  // Nested sheets (New project → Choose a folder → …) keep a link to the sheet that opened them: Back,
  // Cancel on a question and Escape go one level up with the parent's state intact; the scrim and a
  // swipe down close them all.
  function nest(next) { next.parent = S.sheet || null; S.sheet = next; render(); }
  function up() {
    const p = S.sheet && S.sheet.parent;
    S.sheet = p || null;
    if (p) p.steady = true;
    render();
  }
  function newProject() { sugCache.clear(); S.sheet = { kind: 'new', name: '', description: '', folders: [], primary: null }; render(); }
  function sheet(sh) {
    const shut = () => { S.sheet = null; render(); };
    const back = () => { if (S.sheet === sh) up(); };
    const box = h('div', { class: 'hxp-sheet', role: 'dialog', 'aria-modal': 'true' });
    const scrim = h('div', { class: 'hxp-scrim', onclick: (e) => { if (e.target === scrim) shut(); } }, box);
    const head = (title, sub) => h('div', { class: 'hxp-sheet-h' }, btn('hx-back', 'Back', back, 'ArrowLeft01Icon', { 'aria-label': sh.parent ? 'Back' : 'Close' }),
      h('div', { class: 'hxp-title' }, h('span', { text: title }), sub ? h('em', { text: sub }) : null));
    const err = sh.error ? h('p', { class: 'hxp-err', text: sh.error }) : null;
    if (sh.kind === 'new') box.append(head('New project'), sheetNew(sh));
    else if (sh.kind === 'picker') box.append(head('Choose a folder', 'on this computer'), sheetPicker(sh));
    else if (sh.kind === 'confirm') box.append(head(sh.title), h('p', { text: sh.text }),
      h('div', { class: 'hxp-row hxp-row-end' }, btn('hxp-ghost', 'Cancel', back),
        btn(`hxp-primary${sh.danger ? ' is-danger' : ''}`, sh.ok || 'OK', () => act(async () => { await sh.run(); S.sheet = null; }, sh.done || 'Done'))));
    else if (sh.kind === 'attach') box.append(head(sh.chks.length > 1 ? `Attach ${sh.chks.length} folders?` : 'Attach this folder?'), sheetAttach(sh));
    else if (sh.kind === 'delete') box.append(head(`Delete ${sh.p.name}?`), sheetDelete(sh));
    else if (sh.kind === 'session') box.append(head('Session', sh.row.started ? new Date(sh.row.started).toLocaleString() : ''), err || sheetSession(sh));
    else if (sh.kind === 'memory') box.append(head(sh.row.title || 'Memory', sh.row.type), err || sheetMemory(sh));
    else if (sh.kind === 'proposal') box.append(head(sh.row.title, sh.row.type), err || sheetProposal(sh));
    else if (sh.kind === 'finding') box.append(head(sh.f.title, SPEC_NAME[sh.f.specialist] || sh.f.specialist), sheetFinding(sh.f));
    else if (sh.kind === 'run') box.append(head('Dream run', sh.row.at ? new Date(sh.row.at).toLocaleString() : ''), err || sheetRun(sh));
    else if (sh.kind === 'file') box.append(head(sh.title), err || (sh.data ? h('pre', { class: 'hxp-pre', text: sh.data.text + (sh.data.truncated ? '\n…(truncated)' : '') }) : loading()));
    swipeDown(box, () => { if (S.sheet === sh) shut(); });
    return scrim;
  }

  function sheetNew(sh) {
    const name = h('input', { value: sh.name, placeholder: 'e.g. Acme', maxlength: '80', oninput: (e) => {
      sh.name = e.target.value; clearTimeout(sh.suggT); sh.suggT = setTimeout(() => loadSugg(sh), 220); } });
    const desc = h('textarea', { rows: '2', value: sh.description, placeholder: 'What is it? (optional)', maxlength: '600', oninput: (e) => { sh.description = e.target.value; } });
    const fl = h('div', { class: 'hxp-list' });
    for (const f of sh.folders) {
      fl.append(h('div', { class: 'hxp-item' },
        h('input', { type: 'radio', name: 'hxp-primary', checked: (sh.primary || sh.folders[0].path) === f.path || null, onchange: () => { sh.primary = f.path; }, 'aria-label': `Make ${f.display} primary` }),
        h('div', { class: 'hxp-item-b' }, h('strong', { text: f.display.split('/').pop() }), h('div', { class: 'hxp-item-s', text: f.display })),
        btn('hxp-ib', '', () => { sh.folders = sh.folders.filter((x) => x.path !== f.path); render(); }, 'Cancel01Icon', { 'aria-label': 'Remove' })));
    }
    if (!sh.folders.length) fl.append(h('p', { class: 'hxp-muted', text: 'Add every folder or repo that belongs to this project. You can add more later.' }));
    else fl.append(h('p', { class: 'hxp-foot', text: 'The selected one is the primary folder: where agents start.' }));
    return h('div', { class: 'hxp-form' },
      h('label', {}, h('span', { text: 'Name' }), name),
      h('label', {}, h('span', { text: 'Description' }), desc),
      h('div', { class: 'hxp-row' }, h('strong', { text: 'Folders' }), btn('hxp-ghost hxp-sm', 'Add folder', () => openPicker({ forNew: sh }), 'FolderAddIcon')),
      fl,
      suggBox(sh),
      h('div', { class: 'hxp-row hxp-row-end' }, btn('hxp-primary', 'Create project', () => act(async () => {
        if (!sh.name.trim()) throw new Error('Give the project a name.');
        const p = await call('/projects/create', { name: sh.name, description: sh.description, folders: sh.folders.map((f) => f.path), primary: sh.primary });
        try { if (p.folders.length) await ensureWebuiProject(p); } catch (_) {}
        S.sheet = null; S.tab = 'folders';
        await loadProject(p.id);
        return p;
      }, (p) => `Created ${p.name}${p.claimed_sessions ? ` · ${plural(p.claimed_sessions, 'past session')} attached` : ''}`), 'Tick02Icon', { disabled: S.busy || null })));
  }

  // ── repo suggestions: unclaimed git repos, most recently active first, or ranked against the name being
  // typed (folder / package.json / remote / README title; hmem/repo_suggest.py). The server caches its
  // disk scan; this caches answers per query so retyping costs nothing.
  const sugCache = new Map();
  function suggestFor(q) {
    const k = (q || '').trim().toLowerCase();
    if (sugCache.has(k)) return Promise.resolve(sugCache.get(k));
    return get('/fs/repos', k ? { q: k } : null).then((d) => {
      sugCache.set(k, d);
      if (sugCache.size > 50) sugCache.delete(sugCache.keys().next().value);
      return d;
    });
  }
  const repoSub = (r) => r.why || `${r.display}${r.modified ? ' · ' + ago(new Date(r.modified * 1000).toISOString()) : ''}`;
  function loadSugg(sh) {
    const k = (sh.name || '').trim().toLowerCase();
    if (sh.sugg && sh.suggK === k) return paintSugg(sh);
    const seq = (sh.suggSeq || 0) + 1; sh.suggSeq = seq;
    suggestFor(k).then((d) => { if (sh.suggSeq === seq) { sh.sugg = d; sh.suggK = k; paintSugg(sh); } })
      .catch(() => { if (sh.suggSeq === seq) { sh.sugg = { repos: [], failed: true }; sh.suggK = k; paintSugg(sh); } });
  }
  // the list repaints alone while typing: re-rendering the sheet would take the caret out of the name field
  function paintSugg(sh) { if (sh.suggBox && sh.suggBox.isConnected) sh.suggBox.replaceChildren(...suggRows(sh)); }
  function suggBox(sh) {
    sh.suggBox = h('div', { class: 'hxp-sugg', 'aria-live': 'polite' }, ...suggRows(sh));
    if (!sh.sugg || sh.suggK !== (sh.name || '').trim().toLowerCase()) loadSugg(sh);
    return sh.suggBox;
  }
  function suggRows(sh) {
    const d = sh.sugg;
    if (!d) return [h('p', { class: 'hxp-sugg-h', text: 'Suggested repos' }), loading()];
    const have = new Set(sh.folders.map((f) => f.path));
    const rows = (d.repos || []).filter((r) => !have.has(r.path)).slice(0, 5);
    if (!rows.length) return d.failed ? [] : [h('p', { class: 'hxp-sugg-h', text: 'Suggested repos' }),
      h('p', { class: 'hxp-muted', text: 'No other unclaimed git repos found. Use Add folder to browse.' })];
    const q = (sh.name || '').trim();
    const head = d.matched ? `Suggested for “${q}”` : q ? `Nothing named like “${q}” · recently active repos` : 'Suggested repos · recently active';
    return [h('p', { class: 'hxp-sugg-h', text: head }), h('div', { class: 'hxp-list' }, ...rows.map((r) =>
      h('button', { type: 'button', class: 'hxp-item hxp-click', title: r.display, onclick: () => addSugg(sh, r), 'aria-label': `Add ${r.name}` },
        h('span', { class: 'hxp-item-i', icon: 'GitBranchIcon', size: 17 }),
        h('div', { class: 'hxp-item-b' }, h('strong', { class: 'hxp-clip', text: r.name }), h('div', { class: 'hxp-item-s', text: repoSub(r) })),
        h('span', { class: 'hxp-i hxp-sugg-add', icon: 'Add01Icon', size: 16 }))))];
  }
  async function addSugg(sh, r) {
    if (S.busy || sh.folders.some((f) => f.path === r.path)) return;
    const chk = await get('/fs/check', { path: r.path }).catch((e) => ({ ok: false, why: e.message }));
    if (!chk.ok) return toast(`${r.name}: ${chk.why}`, 'error');
    if (chk.project) { sugCache.clear(); sh.sugg = null; return toast(`${chk.display} already belongs to “${chk.project}”.`, 'error'); }
    if (!sh.folders.some((f) => f.path === chk.path)) sh.folders.push({ path: chk.path, display: chk.display });  // the first one is primary
    if (S.sheet === sh) { sh.steady = true; render(); }
  }

  // folder picker: browse folders on the computer (names only) + suggested repos + a typed path
  function openPicker(ctx) {
    const sh = { kind: 'picker', ctx, path: null, data: null, repos: null, mode: 'repos', sel: new Map() };
    if (ctx.forNew && S.sheet === ctx.forNew) nest(sh); else { S.sheet = sh; render(); }
    loadPicker(null);
    // suggestions ranked against the project's name (the one being typed, or the open project's)
    const q = ((ctx.forNew && ctx.forNew.name) || (ctx.forProject && ctx.forProject.name) || '').trim();
    suggestFor(q).then((d) => { sh.repos = d.repos; sh.reposQ = d.matched ? d.query : ''; if (S.sheet === sh) render(); })
      .catch((e) => { sh.repos = []; sh.reposErr = e.message; if (S.sheet === sh) render(); });
  }
  async function loadPicker(path) {
    const sh = S.sheet;
    try { sh.data = await get('/fs/list', path ? { path } : null); sh.error = null; } catch (e) { sh.error = e.message; }
    render();
  }
  // tick folders across tabs and levels, then check them all on the server and confirm once
  function toggle(sh, path, display) {
    if (sh.sel.has(path)) sh.sel.delete(path); else sh.sel.set(path, display);
    render();
  }
  async function chooseAll(paths) {
    const sh = S.sheet;
    const ok = [];
    for (const path of paths) {
      const chk = await get('/fs/check', { path }).catch((e) => ({ ok: false, why: e.message }));
      if (!chk.ok) toast(`${path.split('/').pop()}: ${chk.why}`, 'error');
      else if (chk.project) toast(`${chk.display} already belongs to “${chk.project}”.`, 'error');
      else if (!ok.some((c) => c.path === chk.path)) ok.push(chk);
    }
    if (!ok.length) return;
    if (sh.ctx.forNew) {
      const n = sh.ctx.forNew;
      for (const c of ok) if (!n.folders.some((f) => f.path === c.path)) n.folders.push({ path: c.path, display: c.display });
      S.sheet = n; n.steady = true; render();
      return;
    }
    const have = new Set((sh.ctx.forProject.folders || []).map((f) => f.path));
    const fresh = ok.filter((c) => !have.has(c.path));
    if (!fresh.length) return toast('Those folders are already in this project.', 'error');
    if (S.sheet === sh) nest({ kind: 'attach', p: sh.ctx.forProject, chks: fresh, primary: null });
  }
  const choose = (path) => chooseAll([path]);
  const tick = (sh, path, display) => h('input', { type: 'checkbox', class: 'hxp-tick', checked: sh.sel.has(path) || null,
    onclick: (e) => e.stopPropagation(), onchange: () => toggle(sh, path, display), 'aria-label': `Select ${display}` });
  function sheetPicker(sh) {
    const out = h('div', { class: 'hxp-form' });
    const seg = h('div', { class: 'hxp-seg' },
      btn(`hxp-seg-b${sh.mode === 'repos' ? ' is-on' : ''}`, 'Suggested repos', () => { sh.mode = 'repos'; render(); }),
      btn(`hxp-seg-b${sh.mode === 'browse' ? ' is-on' : ''}`, 'Browse', () => { sh.mode = 'browse'; render(); }),
      btn(`hxp-seg-b${sh.mode === 'path' ? ' is-on' : ''}`, 'Type a path', () => { sh.mode = 'path'; render(); }));
    out.append(seg);
    if (sh.mode === 'repos') {
      const l = h('div', { class: 'hxp-list hxp-scroll' });
      if (!sh.repos) l.append(loading());
      else if (!sh.repos.length) l.append(h('p', { class: 'hxp-muted', text: sh.reposErr || 'No unclaimed git repos found in your home folder or code folders (~/Developer, ~/Projects, ~/code…). Browse instead.' }));
      else out.append(h('p', { class: 'hxp-sugg-h', text: sh.reposQ ? `Best matches for “${sh.reposQ}”` : 'Most recently active' }));
      const have = new Set(((sh.ctx.forNew && sh.ctx.forNew.folders) || []).map((f) => f.path));  // already on the form
      for (const r of (sh.repos || []).filter((x) => !have.has(x.path))) l.append(h('div', { class: `hxp-item hxp-click${sh.sel.has(r.path) ? ' is-sel' : ''}`, onclick: () => toggle(sh, r.path, r.display) },
        h('span', { class: 'hxp-item-i', icon: 'GitBranchIcon', size: 17 }), h('div', { class: 'hxp-item-b' }, h('strong', { text: r.name }), h('div', { class: 'hxp-item-s', text: repoSub(r) })),
        tick(sh, r.path, r.display)));
      out.append(l);
    } else if (sh.mode === 'browse') {
      const d = sh.data;
      if (sh.error) out.append(h('p', { class: 'hxp-err', text: sh.error }));
      if (!d) { out.append(loading()); return out; }
      out.append(h('div', { class: 'hxp-row' },
        d.up ? btn('hxp-ib', '', () => loadPicker(d.up), 'ArrowLeft01Icon', { 'aria-label': 'Up one folder' }) : null,
        h('strong', { class: 'hxp-clip', text: d.display })));
      const l = h('div', { class: 'hxp-list hxp-scroll' });
      for (const e of d.entries) {
        l.append(h('div', { class: `hxp-item${e.attachable ? '' : ' is-dim'}${sh.sel.has(e.path) ? ' is-sel' : ''}` },
          h('button', { type: 'button', class: 'hxp-item-link', onclick: () => loadPicker(e.path), 'aria-label': `Open ${e.name}` },
            h('span', { class: 'hxp-item-i', icon: e.git ? 'GitBranchIcon' : 'Folder01Icon', size: 17 }),
            h('div', { class: 'hxp-item-b' }, h('strong', { text: e.name }),
              e.project ? h('div', { class: 'hxp-item-s', text: `in ${e.project}` }) : (!e.attachable && e.why ? h('div', { class: 'hxp-item-s', text: e.why }) : null))),
          e.attachable && !e.project ? tick(sh, e.path, e.display) : null));
      }
      if (!d.entries.length) l.append(h('p', { class: 'hxp-muted', text: 'No sub-folders here.' }));
      out.append(l);
      if (d.up && !sh.sel.has(d.path)) out.append(h('div', { class: 'hxp-row hxp-row-end' }, btn('hxp-ghost hxp-sm', `Select ${d.display.split('/').pop()}`, () => toggle(sh, d.path, d.display), 'Add01Icon')));
    } else {
      const inp = h('input', { placeholder: '~/acme-app', autocapitalize: 'off', autocorrect: 'off', spellcheck: 'false' });
      const add = async () => {
        const v = inp.value.trim(); if (!v) return;
        const chk = await get('/fs/check', { path: v }).catch((e) => ({ ok: false, why: e.message }));
        if (!chk.ok) return toast(chk.why, 'error');
        if (chk.project) return toast(`That folder already belongs to “${chk.project}”.`, 'error');
        sh.sel.set(chk.path, chk.display); render();
      };
      inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') add(); });
      out.append(h('label', {}, h('span', { text: 'Folder on the computer running Hermes' }), inp),
        h('div', { class: 'hxp-row hxp-row-end' }, btn('hxp-ghost hxp-sm', 'Check and select', add, 'Search01Icon')));
    }
    if (sh.sel.size) out.append(h('div', { class: 'hxp-selbar' },
      h('div', { class: 'hxp-chips' }, ...[...sh.sel].map(([path, display]) => h('button', { type: 'button', class: 'hxp-chip', title: display, onclick: () => toggle(sh, path, display), 'aria-label': `Unselect ${display}` },
        h('span', { text: display.split('/').pop() }), h('span', { class: 'hxp-i', icon: 'Cancel01Icon', size: 12 })))),
      btn('hxp-primary', sh.sel.size === 1 ? 'Add 1 folder' : `Add ${sh.sel.size} folders`, () => act(() => chooseAll([...sh.sel.keys()])), 'Tick02Icon', { disabled: S.busy || null })));
    out.append(h('p', { class: 'hxp-foot', text: 'This is the computer’s filesystem, even when you’re on your phone. Hidden, personal (Documents, Desktop, Downloads…) and Hermes’ own folders can’t be attached.' }));
    return out;
  }
  function sheetAttach(sh) {
    const many = sh.chks.length > 1;
    const list = h('div', { class: 'hxp-list' });
    for (const c of sh.chks) list.append(h('div', { class: 'hxp-card hxp-note' }, h('span', { class: 'hxp-item-i', icon: c.git ? 'GitBranchIcon' : 'Folder01Icon', size: 20 }),
      h('div', { class: 'hxp-item-b' }, h('strong', { class: 'hxp-break', text: c.display }), h('p', { class: 'hxp-muted', text: c.git ? `git repo${c.git.remote ? ' · ' + c.git.remote : ''}` : 'not a git repo' }),
        c.context_files.length ? h('p', { class: 'hxp-muted', text: `has ${c.context_files.slice(0, 4).join(', ')}${c.context_files.length > 4 ? '…' : ''}` }) : null),
      h('label', { class: 'hxp-toggle' }, h('input', { type: many ? 'radio' : 'checkbox', name: 'hxp-att-primary', checked: sh.primary === c.path || null,
        onchange: (e) => { sh.primary = e.target.checked ? c.path : null; } }), h('span', { text: 'primary' }))));
    return h('div', { class: 'hxp-form' }, list,
      h('p', { class: 'hxp-muted', text: `Agents working on ${sh.p.name} will be able to read and edit everything in ${many ? 'these folders' : 'this folder'}.` }),
      h('div', { class: 'hxp-row hxp-row-end' }, btn('hxp-ghost', 'Cancel', () => { S.sheet = null; render(); }),
        btn('hxp-primary', many ? `Attach ${sh.chks.length} folders` : 'Attach', () => act(async () => {
          let hash = sh.p.hash, claimed = 0, done = 0;
          for (const c of sh.chks) {  // one at a time: each attach returns the hash the next one must match
            const x = await call('/projects/folder/add', { id: sh.p.id, hash, path: c.path, confirm_path: c.path, primary: sh.primary === c.path });
            hash = x.hash; claimed += x.claimed_sessions || 0; done++;
          }
          S.sheet = null;
          await loadProject(sh.p.id, true);
          if (S.p) ensureWebuiProject(S.p).catch(() => {});
          return { done, claimed };
        }, (x) => `Attached ${plural(x.done, 'folder')}${x.claimed ? ` · ${plural(x.claimed, 'past session')} now in this project` : ''}`), 'Tick02Icon', { disabled: S.busy || null })));
  }
  function sheetDelete(sh) {
    const inp = h('input', { placeholder: sh.p.name });
    return h('div', { class: 'hxp-form' },
      h('p', { text: 'The project, its memory and its settings leave Hermes. Your folders are not touched. Everything stays in the memory history and can be recovered from the computer.' }),
      h('label', {}, h('span', { text: `Type “${sh.p.name}” to confirm` }), inp),
      h('div', { class: 'hxp-row hxp-row-end' }, btn('hxp-ghost', 'Cancel', () => { S.sheet = null; render(); }),
        btn('hxp-primary is-danger', 'Delete project', () => act(async () => {
          await call('/projects/delete', { id: sh.p.id, confirm_name: inp.value });
          if (sh.p.webui_project_id) webui('/api/projects/delete', { project_id: sh.p.webui_project_id }).catch(() => {});
          S.sheet = null; toList();
        }, 'Deleted'), 'Delete02Icon')));
  }
  function kv(label, value) { return value == null || value === '' ? null : h('div', { class: 'hxp-kv' }, h('span', { text: label }), h('strong', { text: String(value) })); }
  function listBlock(title, items, max) {
    if (!items || !items.length) return null;
    return h('div', {}, h('h3', { class: 'hxp-h3', text: `${title} (${items.length})` }),
      h('ul', { class: 'hxp-ul' }, ...items.slice(0, max || 30).map((x) => h('li', { text: typeof x === 'string' ? x : JSON.stringify(x) }))));
  }
  function sheetSession(sh) {
    const d = sh.data;
    if (!d) return loading();
    const out = h('div', { class: 'hxp-form' },
      h('p', { class: 'hxp-quote', text: d.user_request || '' }),
      h('div', { class: 'hxp-kvs' }, kv('Outcome', (d.outcome || {}).label), kv('Folders', (d.folders || []).join(', ')),
        kv('Models', (d.models || []).join(', ')), kv('Duration', d.duration_s ? `${Math.round(d.duration_s)} s` : null),
        kv('Tokens', d.tokens || d.output_tokens), kv('Retries', d.retries)),
      listBlock('Files changed', d.files_modified), listBlock('Errors', (d.errors || []).map((e) => `${e.tool}: ${(e.text || '').split('\n').slice(0, 3).join(' ')}`), 12),
      listBlock('Commands', d.commands, 20), d.final_result ? h('div', {}, h('h3', { class: 'hxp-h3', text: 'Result' }), h('pre', { class: 'hxp-pre', text: d.final_result })) : null);
    if (sh.row.webui_session && typeof loadSession === 'function') out.append(h('div', { class: 'hxp-row hxp-row-end' },
      btn('hxp-primary hxp-sm', 'Open chat', () => { close(); loadSession(sh.row.webui_session); }, 'BubbleChatIcon')));
    return out;
  }
  function sheetMemory(sh) {
    const d = sh.data;
    if (!d) return loading();
    const m = d.meta || {};
    return h('div', { class: 'hxp-form' },
      h('pre', { class: 'hxp-pre hxp-prose', text: d.body }),
      h('div', { class: 'hxp-kvs' }, kv('Trust', m.trust), kv('Version', m.version), kv('Status', m.status), kv('Written by', m.created_by),
        kv('Approved by', m.approved_by), kv('Updated', m.updated_at ? new Date(m.updated_at).toLocaleString() : null),
        kv('Evidence', (m.source_sessions || []).length ? plural(m.source_sessions.length, 'session') : null), kv('Copy of', m.mirror_of)),
      listBlock('History', (d.history || []).map((x) => `${(x.date || '').slice(0, 10)} · ${x.actor} · ${x.subject}`), 10),
      S.p && ownMem(S.p, sh.row) ? h('div', { class: 'hxp-row hxp-row-end' },
        btn('hxp-ghost is-danger', 'Delete memory', () => deleteMemory(S.p, sh.row), 'Delete02Icon')) : null);
  }
  function diffView(text) {
    const pre = h('pre', { class: 'hxp-pre hxp-diff' });
    for (const ln of (text || '(no change)').split('\n')) pre.append(h('span', { class: ln.startsWith('+') && !ln.startsWith('+++') ? 'is-add' : ln.startsWith('-') && !ln.startsWith('---') ? 'is-del' : '', text: ln + '\n' }));
    return pre;
  }
  function sheetProposal(sh) {
    const d = sh.data;
    if (!d) return loading();
    const p = S.p;
    const pending = d.status === 'pending';
    const out = h('div', { class: 'hxp-form' },
      h('p', { text: d.reason }),
      h('div', { class: 'hxp-kvs' }, kv('Kind', d.type), kv('Target', d.target), kv('Sessions', d.sessions), kv('Confidence', d.confidence != null ? `${Math.round(d.confidence * 100)}%` : null),
        kv('Risk', d.risk), kv('Source trust', d.trust), kv('Proposed by', d.created_by)));
    if (d.flags.length) out.append(h('div', { class: 'hxp-warn' }, h('strong', { text: 'Check before approving' }), h('ul', { class: 'hxp-ul' }, ...d.flags.map((f) => h('li', { text: f })))));
    out.append(h('h3', { class: 'hxp-h3', text: 'Proposed change' }), diffView(d.diff));
    out.append(h('h3', { class: 'hxp-h3', text: `Evidence (${(d.evidence || []).length})` }));
    const ev = h('div', { class: 'hxp-list' });
    for (const e of (d.evidence || []).slice(0, 12)) ev.append(h('div', { class: 'hxp-ev' },
      e.session ? h('span', { class: 'hxp-tag', text: e.session }) : null,
      h('pre', { class: 'hxp-pre hxp-pre-sm', text: e.quote || e.memory || JSON.stringify(e, null, 1) })));
    out.append(ev);
    if (d.note) out.append(h('p', { class: 'hxp-err', text: d.note }));
    if (pending && p) {
      if (d.flags.length) out.append(h('label', { class: 'hxp-toggle' }, h('input', { type: 'checkbox', onchange: (e) => { sh.confirm = e.target.checked; render(); }, checked: sh.confirm || null }), h('span', { text: 'I’ve read the evidence and the change' })));
      const reason = h('input', { placeholder: 'Why not? (optional, helps the Dreamer)' });
      out.append(h('div', { class: 'hxp-row hxp-row-end' },
        d.blocked ? h('span', { class: 'hxp-err', text: 'Blocked by the safety scan: it can’t be approved as written.' }) : null,
        btn('hxp-ghost', 'Reject', () => { nest({ kind: 'confirm', title: 'Reject this proposal?', text: 'The Dreamer won’t propose the same thing again for 30 days.', ok: 'Reject', danger: true,
          run: () => call('/projects/proposal/reject', { id: p.id, pid: d.id, reason: reason.value }).then(() => { S.cache = {}; return loadProject(p.id, true); }), done: 'Rejected' }); }, 'Cancel01Icon'),
        btn('hxp-primary', 'Approve', () => act(async () => {
          await call('/projects/proposal/approve', { id: p.id, pid: d.id, confirm: sh.confirm === true });
          S.sheet = null; S.cache = {}; await loadProject(p.id, true);
        }, { deprecate: 'Approved: that memory is retired', skill: 'Approved: skill installed for this project' }[d.type] || 'Approved: it’s now part of this project’s memory'), 'Tick02Icon', { disabled: d.blocked || (d.flags.length && !sh.confirm) || S.busy || null })), reason);
    }
    return out;
  }
  function sheetFinding(f) {
    return h('div', { class: 'hxp-form' },
      f.detail ? h('p', { text: f.detail }) : null,
      h('div', { class: 'hxp-kvs' }, kv('Specialist', SPEC_NAME[f.specialist] || f.specialist), kv('Sessions', f.affected_sessions != null ? `${f.affected_sessions} of ${f.relevant_sessions}` : null),
        kv('Conversations', f.conversations), kv('Days', f.days), kv('Confidence', f.confidence != null ? `${Math.round(f.confidence * 100)}%` : null),
        kv('Folders', (f.folders || []).join(', ')), kv('Recommended', f.recommended_action ? `${f.recommended_action.type} → ${f.recommended_action.target}` : null),
        kv('Status', f.report_only ? 'for your review (no proposal made)' : 'proposal made if it passed the checks')),
      h('h3', { class: 'hxp-h3', text: 'Evidence' }),
      ...(f.evidence || []).map((e) => h('pre', { class: 'hxp-pre hxp-pre-sm', text: typeof e === 'string' ? e : JSON.stringify(e, null, 1) })),
      listBlock('Sessions', f.sessions, 20));
  }
  function sheetRun(sh) {
    const d = sh.data;
    if (!d) return loading();
    const l = h('div', { class: 'hxp-list' });
    for (const f of d.findings) l.append(h('button', { type: 'button', class: 'hxp-item hxp-click', onclick: () => nest({ kind: 'finding', f }) },
      h('span', { class: 'hxp-item-i', icon: SPEC_ICON[f.specialist] || 'SparklesIcon', size: 17 }),
      h('div', { class: 'hxp-item-b' }, h('span', { class: 'hxp-clip', text: f.title }), h('div', { class: 'hxp-meta' }, h('span', { text: SPEC_NAME[f.specialist] || f.specialist }))),
      h('span', { class: 'hxp-i', icon: 'ArrowRight01Icon', size: 16 })));
    return h('div', { class: 'hxp-form' },
      h('div', { class: 'hxp-kvs' }, kv('Trigger', d.trigger), kv('Sessions analysed', d.traces_analysed), kv('Took', `${d.seconds} s`),
        kv('Specialists', (d.specialists || []).map((s) => `${s.specialist} ${s.findings}`).join(' · '))),
      d.findings.length ? l : h('p', { class: 'hxp-muted', text: 'Nothing found in this run.' }),
      listBlock('Not proposed', (d.skipped || []).map((s) => `${s.signature}: ${s.why}`), 20));
  }

  // ── boot ──
  window.hxProjects = { open, close, isOpen };
  if (window.__hxBootPanel === 'projects') bootRoute = fromHash(location.hash);  // landed on /projects#<id>[/<tab>]
  function start() {
    syncNav();
    new MutationObserver(() => { if (!document.getElementById('hxProjRail') || !document.getElementById('hxProjTab')) navButtons(); })
      .observe(document.body, { childList: true, subtree: true });
    setInterval(() => { if (isOpen()) place(); }, 1000);
    setTimeout(refreshBadge, 3000);  // after the app has settled
    setInterval(() => { if (!document.hidden) refreshBadge(); }, 5 * 60 * 1000);
    document.addEventListener('visibilitychange', () => { if (!document.hidden && Date.now() - badgeAt > 60 * 1000) refreshBadge(); });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
