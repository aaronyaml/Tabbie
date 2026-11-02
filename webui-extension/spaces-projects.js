/* Spaces & Projects — Hermes WebUI extension (plain JS, no build step).
 *  1. "New project" sheet: name, colour, checkbox list of spaces with one default.
 *     Uses /api/projects/create + /api/projects/bind. Same sheet edits an
 *     existing project's spaces (chip menu -> "Spaces…", or the picker in the sheet).
 *  2. Immediate child folders of a space (non-hidden dirs, sorted) in the space
 *     detail view, the workspace picker dropdown and the project sheet.
 *     Read-only, via the existing /api/workspaces/suggest endpoint.
 */
(() => {
  'use strict';
  if (window.__hxSpacesProjects) return;
  window.__hxSpacesProjects = true;

  const FALLBACK_COLORS = ['#7cb9ff', '#f5c542', '#e94560', '#50c878', '#c084fc', '#fb923c', '#67e8f9', '#f472b6'];
  const colors = () => {
    try { if (Array.isArray(PROJECT_COLORS) && PROJECT_COLORS.length) return PROJECT_COLORS; } catch (_) {}
    return FALLBACK_COLORS;
  };

  // ── helpers ──────────────────────────────────────────────────────────────
  function h(tag, props, ...kids) {
    const n = document.createElement(tag);
    if (props) for (const [k, v] of Object.entries(props)) {
      if (v == null || v === false) continue;
      if (k === 'class') n.className = v;
      else if (k === 'text') n.textContent = v;
      else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? '' : v);
    }
    for (const k of kids.flat()) if (k != null) n.append(k.nodeType ? k : String(k));
    return n;
  }
  function call(path, body) {
    const opts = body === undefined ? {} : { method: 'POST', body: JSON.stringify(body) };
    if (typeof api === 'function') return api(path, opts);
    return fetch(path.replace(/^\//, ''), { credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, ...opts })
      .then(async (r) => { const j = await r.json().catch(() => ({})); if (!r.ok) throw new Error(j.error || r.statusText); return j; });
  }
  function toast(msg, kind) { if (typeof showToast === 'function') showToast(msg, kind); else console.log('[spaces-projects]', msg); }
  async function refreshSidebar() {
    try { if (typeof renderSessionList === 'function') await renderSessionList({ deferWhileInteracting: false }); } catch (_) {}
  }
  const LOCAL_ICONS = {
    'folder-plus': '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/><line x1="12" y1="10" x2="12" y2="16"/><line x1="9" y1="13" x2="15" y2="13"/>',
    folder: '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>',
    x: '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
    'refresh-cw': '<polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>',
  };
  const icon = (name, size) => {
    const s = h('span', { class: 'hxsp-ico' });
    let svg = '';
    try { if (typeof LI_PATHS === 'object' && LI_PATHS[name] && typeof li === 'function') svg = li(name, size || 14); } catch (_) {}
    if (!svg) svg = `<svg width="${size || 14}" height="${size || 14}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${LOCAL_ICONS[name] || ''}</svg>`;
    s.innerHTML = svg;
    return s;
  };
  const baseName = (p) => String(p || '').replace(/\/+$/, '').split('/').pop() || p;

  // ── child folders (via /api/workspaces/suggest) ──────────────────────────
  // suggest returns at most 12 entries (incl. saved roots that match the prefix),
  // so when a query is saturated we refine by the next character of the name.
  const LIMIT = 12;
  const FIRST = 'abcdefghijklmnopqrstuvwxyz0123456789_-@+#$%&()[]{}!=,;\'^~';
  const NEXT = FIRST + '.';
  const cache = new Map();

  function pool(limit) {
    let active = 0; const q = [];
    const next = () => { if (active >= limit || !q.length) return; active++; const [fn, res, rej] = q.shift(); fn().then(res, rej).finally(() => { active--; next(); }); };
    return (fn) => new Promise((res, rej) => { q.push([fn, res, rej]); next(); });
  }

  async function fetchChildren(base) {
    base = String(base).replace(/\/+$/, '');
    const sep = base + '/';
    const names = new Set();
    let incomplete = false;
    const run = pool(8);
    const suggest = (leaf) => run(() => call('/api/workspaces/suggest?prefix=' + encodeURIComponent(sep + leaf)));
    async function q(leaf, depth) {
      const r = await suggest(leaf);
      const list = (r && r.suggestions) || [];
      let last = '';
      for (const s of list) {
        if (!s.startsWith(sep)) continue;
        const rest = s.slice(sep.length);
        if (rest && !rest.includes('/') && !rest.startsWith('.')) {
          names.add(rest);
          const low = rest.toLowerCase();
          if (low.startsWith(leaf.toLowerCase()) && low > last) last = low;
        }
      }
      if (list.length < LIMIT) return;
      if (depth >= 4) { incomplete = true; return; }
      // The server lists children sorted by lower-cased name and truncates the
      // tail, so only names sorting at/after the last one returned can be missing.
      const floor = last ? last.slice(0, leaf.length + 1) : '';
      const chars = [...(depth ? NEXT : FIRST)].filter((c) => (leaf + c).toLowerCase() >= floor);
      await Promise.all(chars.map((c) => q(leaf + c, depth + 1)));
    }
    await q('', 0);
    return { names: [...names].sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base', numeric: true })), incomplete };
  }
  function getChildren(path, fresh) {
    const hit = cache.get(path);
    if (hit && !fresh && Date.now() - hit.t < 30000) return hit.p;
    const p = fetchChildren(path).catch((e) => { cache.delete(path); throw e; });
    cache.set(path, { t: Date.now(), p });
    return p;
  }

  // Renders the folder list into `box` (a container owned by this extension).
  async function fillFolders(box, path, fresh) {
    box.replaceChildren(h('div', { class: 'hxsp-muted', text: 'Loading folders…' }));
    try {
      const { names, incomplete } = await getChildren(path, fresh);
      if (!box.isConnected) return;
      if (!names.length) { box.replaceChildren(h('div', { class: 'hxsp-muted', text: 'No subfolders' })); return; }
      const ul = h('ul', { class: 'hxsp-folder-list' }, names.map((n) => h('li', { title: path.replace(/\/+$/, '') + '/' + n }, icon('folder', 12), h('span', { text: n }))));
      box.replaceChildren(ul);
      if (incomplete) box.append(h('div', { class: 'hxsp-muted', text: 'List may be incomplete.' }));
    } catch (e) {
      if (box.isConnected) box.replaceChildren(h('div', { class: 'hxsp-muted', text: 'Could not list folders: ' + ((e && e.message) || e) }));
    }
  }

  // 2a. Space detail view (Spaces panel → select a space).
  function currentDetailPath(body) {
    try { if (_currentWorkspaceDetail && _currentWorkspaceDetail.path) return _currentWorkspaceDetail.path; } catch (_) {}
    const code = body.querySelector('.detail-card code');
    return code ? code.textContent.trim() : '';
  }
  function enhanceDetail() {
    const body = document.getElementById('workspaceDetailBody');
    if (!body || !body.querySelector('#checkpointListContainer')) return; // read mode only
    const path = currentDetailPath(body);
    if (!path) return;
    const existing = body.querySelector('.hxsp-folders-card');
    if (existing && existing.dataset.path === path) return;
    if (existing) existing.remove();
    const list = h('div', { class: 'hxsp-folders-body' });
    const refresh = h('button', { class: 'hxsp-icon-btn', type: 'button', title: 'Refresh', 'aria-label': 'Refresh folders', onclick: () => fillFolders(list, path, true) }, icon('refresh-cw', 13));
    const card = h('div', { class: 'detail-card hxsp-folders-card', 'data-path': path },
      h('div', { class: 'detail-card-title detail-card-title-row' }, h('span', { text: 'Folders' }), refresh),
      list);
    const first = body.querySelector('.detail-card');
    if (first) first.after(card); else (body.querySelector('.main-view-content') || body).append(card);
    fillFolders(list, path);
  }

  // 2b. Workspace picker dropdown rows (.ws-opt[data-path]).
  function enhanceWsOpt(opt) {
    if (opt.dataset.hxsp || !opt.dataset.path || !opt.closest('.ws-list-container')) return;
    opt.dataset.hxsp = '1';
    opt.classList.add('hxsp-ws-opt');
    const path = opt.dataset.path;
    let box = null;
    const btn = h('button', { class: 'hxsp-ws-toggle', type: 'button', title: 'Show folders', 'aria-label': 'Show folders in ' + (opt.dataset.name || path), 'aria-expanded': 'false' }, icon('folder', 13));
    const stop = (e) => { e.stopPropagation(); };
    btn.addEventListener('pointerdown', stop);
    btn.addEventListener('click', (e) => {
      e.preventDefault(); e.stopPropagation();
      if (box) { box.remove(); box = null; btn.setAttribute('aria-expanded', 'false'); btn.classList.remove('on'); return; }
      box = h('div', { class: 'hxsp-ws-children', onclick: stop, onpointerdown: stop });
      opt.append(box); btn.setAttribute('aria-expanded', 'true'); btn.classList.add('on');
      fillFolders(box, path);
    });
    opt.append(btn);
  }

  // 2c. The picker lists projects (Tabbie's multi-folder workspaces, ~/hermes/workspaces/<id>) and spaces
  // (single folders) in one list: show projects first, under their own headings.
  const PROJECT_WS = /\/hermes\/workspaces\/[^/]+\/?$/;
  function groupWsList(list) {
    if (list.dataset.hxspGrouped) return;
    const opts = [...list.querySelectorAll(':scope > .ws-opt[data-path]')];
    const projects = opts.filter((o) => PROJECT_WS.test(o.dataset.path));
    if (!opts.length || !projects.length) return;
    list.dataset.hxspGrouped = '1';
    const spaces = opts.filter((o) => !projects.includes(o));
    const head = (text) => h('div', { class: 'hxsp-ws-head', role: 'presentation', text });
    const first = list.firstChild;
    list.insertBefore(head('Projects'), first);
    projects.forEach((o) => { o.classList.add('hxsp-ws-project'); list.insertBefore(o, first); });
    if (spaces.length) list.insertBefore(head('Spaces'), spaces[0]);
  }

  // ── project sheet ────────────────────────────────────────────────────────
  let sheet = null;
  function closeSheet() {
    if (!sheet) return;
    sheet.remove(); sheet = null;
    document.removeEventListener('keydown', onSheetKey, true);
  }
  function onSheetKey(e) { if (e.key === 'Escape' && sheet) { e.preventDefault(); e.stopPropagation(); closeSheet(); } }

  async function openProjectSheet(projectId) {
    closeSheet();
    let wss = [], projects = [];
    try {
      const [w, p] = await Promise.all([call('/api/workspaces'), call('/api/projects')]);
      wss = (w && w.workspaces) || []; projects = (p && p.projects) || [];
    } catch (e) { toast('Could not load spaces/projects: ' + ((e && e.message) || e), 'error'); return; }
    const proj = projectId ? projects.find((p) => p.project_id === projectId) : null;
    const editing = !!proj;
    const bound = proj ? (Array.isArray(proj.workspaces) ? proj.workspaces : (proj.workspace ? [proj.workspace] : [])) : [];
    // Bound paths not in the saved list still need a row so they are not dropped silently.
    for (const b of bound) if (!wss.some((w) => w.path === b)) wss.push({ path: b, name: baseName(b) });
    const state = {
      selected: new Set(bound),
      def: proj ? (proj.default_workspace || bound[0] || null) : null,
      color: proj ? (proj.color || null) : colors()[projects.length % colors().length],
    };

    const nameInput = h('input', { class: 'app-dialog-input hxsp-name', type: 'text', placeholder: 'Project name', maxlength: '128', autocomplete: 'off', autocapitalize: 'sentences', spellcheck: 'false', 'data-lpignore': 'true', 'data-1p-ignore': 'true' });
    nameInput.value = proj ? proj.name : '';

    const swatches = h('div', { class: 'hxsp-swatches', role: 'radiogroup', 'aria-label': 'Colour' });
    const paintSwatches = () => {
      swatches.replaceChildren(...[null, ...colors()].map((c) => {
        const b = h('button', { type: 'button', class: 'hxsp-swatch' + (c ? '' : ' none') + (state.color === c ? ' on' : ''), role: 'radio', 'aria-checked': String(state.color === c), 'aria-label': c || 'No colour', title: c || 'No colour', onclick: () => { state.color = c; paintSwatches(); } });
        if (c) b.style.background = c;
        return b;
      }));
    };
    paintSwatches();

    const count = h('span', { class: 'hxsp-count' });
    const filter = h('input', { class: 'app-dialog-input hxsp-filter', type: 'search', placeholder: 'Filter spaces', autocomplete: 'off', spellcheck: 'false' });
    const list = h('div', { class: 'hxsp-space-list', role: 'group', 'aria-label': 'Spaces' });

    // Rows are built once and updated in place, so scroll position, focus and
    // expanded folder lists survive toggling.
    const rows = wss.map((w) => {
      const cb = h('input', { type: 'checkbox', class: 'hxsp-cb', 'aria-label': w.name || w.path });
      cb.addEventListener('change', () => { if (cb.checked) state.selected.add(w.path); else state.selected.delete(w.path); paintList(); });
      const defBtn = h('button', { type: 'button', class: 'hxsp-def', onclick: () => { state.def = w.path; paintList(); } });
      const box = h('div', { class: 'hxsp-row-folders', hidden: true });
      const fold = h('button', { type: 'button', class: 'hxsp-icon-btn', title: 'Show folders', 'aria-label': 'Show folders in ' + (w.name || w.path), 'aria-expanded': 'false', onclick: () => {
        const open = box.hidden; box.hidden = !open; fold.setAttribute('aria-expanded', String(open)); fold.classList.toggle('on', open);
        if (open) fillFolders(box, w.path);
      } }, icon('folder', 13));
      const el = h('div', { class: 'hxsp-space' },
        h('div', { class: 'hxsp-space-top' },
          h('label', { class: 'hxsp-space-main' }, cb, h('span', { class: 'hxsp-space-text' }, h('span', { class: 'hxsp-space-name', text: w.name || baseName(w.path) }), h('span', { class: 'hxsp-space-path', text: w.path }))),
          fold, defBtn),
        box);
      return { w, el, cb, defBtn };
    });
    list.append(...rows.map((r) => r.el));

    function paintList() {
      if (state.def && !state.selected.has(state.def)) state.def = null;
      if (!state.def && state.selected.size) state.def = rows.map((r) => r.w.path).find((p) => state.selected.has(p)) || null;
      const term = filter.value.trim().toLowerCase();
      for (const { w, el, cb, defBtn } of rows) {
        const on = state.selected.has(w.path);
        const isDef = on && state.def === w.path;
        cb.checked = on;
        el.classList.toggle('on', on);
        defBtn.disabled = !on;
        defBtn.classList.toggle('on', isDef);
        defBtn.setAttribute('aria-pressed', String(isDef));
        defBtn.title = isDef ? 'Default space for new chats' : 'Make default';
        defBtn.textContent = isDef ? 'Default' : 'Set default';
        el.hidden = !!term && !(String(w.name || '').toLowerCase().includes(term) || w.path.toLowerCase().includes(term));
      }
      count.textContent = state.selected.size ? state.selected.size + ' selected' : 'none selected';
    }
    filter.addEventListener('input', paintList);
    paintList();

    const err = h('div', { class: 'hxsp-error', role: 'alert', hidden: true });
    const saveBtn = h('button', { class: 'app-dialog-btn confirm', type: 'button' }, editing ? 'Save' : 'Create');
    const cancelBtn = h('button', { class: 'app-dialog-btn', type: 'button', onclick: closeSheet }, 'Cancel');

    let switcher = null;
    if (projects.length) {
      switcher = h('select', { class: 'hxsp-switch', 'aria-label': 'Project to edit' },
        h('option', { value: '' }, 'New project'),
        projects.map((p) => h('option', { value: p.project_id }, p.name)));
      switcher.value = proj ? proj.project_id : '';
      switcher.addEventListener('change', () => openProjectSheet(switcher.value || null));
    }

    async function save() {
      const name = nameInput.value.trim();
      err.hidden = true;
      if (!name) { err.textContent = 'Name is required.'; err.hidden = false; nameInput.focus(); return; }
      const selected = wss.map((w) => w.path).filter((p) => state.selected.has(p));
      const bindBody = selected.length ? { workspaces: selected, default_workspace: state.def || selected[0] } : { workspaces: null };
      saveBtn.disabled = true;
      let created = null;
      try {
        let id = proj && proj.project_id;
        if (editing) {
          if (name !== proj.name || (state.color || null) !== (proj.color || null)) {
            await call('/api/projects/rename', { project_id: id, name, color: state.color || '' });
          }
          if (selected.length || bound.length) await call('/api/projects/bind', { project_id: id, ...bindBody });
        } else {
          const r = await call('/api/projects/create', state.color ? { name, color: state.color } : { name });
          created = r && r.project; id = created && created.project_id;
          if (!id) throw new Error('create returned no project');
          if (selected.length) await call('/api/projects/bind', { project_id: id, ...bindBody });
        }
        closeSheet();
        await refreshSidebar();
        toast(editing ? 'Project updated' : 'Project created' + (selected.length ? ' with ' + selected.length + (selected.length === 1 ? ' space' : ' spaces') : ''));
      } catch (e) {
        // A bind failure after create would leave a half-made project: roll it back.
        if (created && created.project_id) { try { await call('/api/projects/delete', { project_id: created.project_id }); } catch (_) {} }
        err.textContent = (editing ? 'Save failed: ' : 'Create failed: ') + ((e && e.message) || e);
        err.hidden = false;
        saveBtn.disabled = false;
      }
    }
    saveBtn.addEventListener('click', save);
    nameInput.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.isComposing) { e.preventDefault(); save(); } });

    const dialog = h('div', { class: 'app-dialog hxsp-dialog', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'hxspTitle' },
      h('div', { class: 'app-dialog-header' },
        h('div', { class: 'app-dialog-title', id: 'hxspTitle', text: editing ? 'Edit project' : 'New project' }),
        h('div', { class: 'hxsp-head-right' }, switcher,
          h('button', { class: 'app-dialog-close', type: 'button', 'aria-label': 'Close', onclick: closeSheet }, icon('x', 14)))),
      h('div', { class: 'hxsp-scroll' },
        h('label', { class: 'hxsp-label', text: 'Name' }), nameInput,
        h('div', { class: 'hxsp-label', text: 'Colour' }), swatches,
        h('div', { class: 'hxsp-label hxsp-label-row' }, h('span', { text: 'Spaces' }), count),
        wss.length > 6 ? filter : null,
        list,
        h('div', { class: 'hxsp-hint', text: 'New chats in this project start in the default space.' })),
      err,
      h('div', { class: 'app-dialog-actions hxsp-actions' }, cancelBtn, saveBtn));
    sheet = h('div', { class: 'app-dialog-overlay hxsp-overlay', 'aria-hidden': 'false' }, dialog);
    sheet.style.display = 'flex';
    sheet.addEventListener('mousedown', (e) => { if (e.target === sheet) closeSheet(); });
    document.body.append(sheet);
    document.addEventListener('keydown', onSheetKey, true);
    // Avoid popping the iOS keyboard over the list when editing.
    if (!editing && !matchMedia('(pointer:coarse)').matches) setTimeout(() => nameInput.focus(), 0);
  }
  window.hxOpenProjectSheet = openProjectSheet;

  // ── entry points ─────────────────────────────────────────────────────────
  function addHeaderButton() {
    const actions = document.querySelector('#panelChat .panel-head-actions');
    if (!actions || actions.querySelector('.hxsp-new-project')) return;
    const b = h('button', { class: 'panel-head-btn hxsp-new-project has-tooltip has-tooltip--bottom', type: 'button', 'data-tooltip': 'New project', 'aria-label': 'New project', onclick: (e) => { e.stopPropagation(); openProjectSheet(null); } }, icon('folder-plus', 16));
    actions.prepend(b);
  }

  // The chip bar's "+" opens the sheet instead of the inline name editor.
  document.addEventListener('click', (e) => {
    const t = e.target && e.target.closest && e.target.closest('.project-bar .project-create-btn');
    if (!t) return;
    e.preventDefault(); e.stopImmediatePropagation();
    openProjectSheet(null);
  }, true);

  // Remember which chip opened the native context menu (right-click / long-press).
  let lastChip = null;
  const noteChip = (e) => { const c = e.target && e.target.closest && e.target.closest('.project-bar .project-chip'); if (c) lastChip = c; };
  document.addEventListener('contextmenu', noteChip, true);
  document.addEventListener('touchstart', noteChip, { capture: true, passive: true });

  async function projectForChip(chip) {
    const bar = chip.closest('.project-bar');
    const chips = bar ? [...bar.querySelectorAll('.project-chip')].filter((c) => !c.classList.contains('no-project') && c.querySelector('span:not(.color-dot)')) : [];
    const name = (chip.querySelector('span:not(.color-dot)') || chip).textContent.trim();
    const { projects = [] } = await call('/api/projects');
    const same = projects.filter((p) => p.name === name);
    if (same.length === 1) return same[0];
    const idx = chips.indexOf(chip);
    return projects[idx] || same[0] || null;
  }
  function enhanceCtxMenu(menu) {
    if (menu.dataset.hxsp || !lastChip) return;
    menu.dataset.hxsp = '1';
    const chip = lastChip;
    const item = h('div', { class: 'hxsp-ctx-item', role: 'menuitem', tabindex: '0', text: 'Spaces…' });
    const go = async () => {
      menu.remove();
      try { const p = await projectForChip(chip); if (p) openProjectSheet(p.project_id); else toast('Project not found', 'error'); }
      catch (e) { toast('Could not open project: ' + ((e && e.message) || e), 'error'); }
    };
    item.addEventListener('click', go);
    item.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } });
    const rename = menu.firstElementChild;
    if (rename) rename.after(item); else menu.prepend(item);
  }

  // Project chips sit in one horizontally scrolling row (spaces-projects.css). Upstream rebuilds the row on
  // every sidebar render: keep the scroll position, and bring the active chip into view.
  let barLeft = 0;
  document.addEventListener('scroll', (e) => {
    if (e.target && e.target.classList && e.target.classList.contains('project-bar')) { barLeft = e.target.scrollLeft; fade(e.target); }
  }, true);
  const fade = (bar) => bar.classList.toggle('hxsp-more', bar.scrollLeft + bar.clientWidth < bar.scrollWidth - 4);
  function fixBar(bar) {
    bar.scrollLeft = barLeft;
    const a = bar.querySelector('.project-chip.active');
    if (!a) { fade(bar); return; }
    const l = a.offsetLeft - bar.offsetLeft, r = l + a.offsetWidth;
    if (l < bar.scrollLeft || r > bar.scrollLeft + bar.clientWidth) bar.scrollLeft = barLeft = Math.max(0, l - 24);
    fade(bar);
  }

  function scan(root) {
    if (!root || root.nodeType !== 1) return;
    if (root.matches('.project-bar')) requestAnimationFrame(() => fixBar(root));
    else if (root.querySelector) { const b = root.querySelector('.project-bar'); if (b) requestAnimationFrame(() => fixBar(b)); }
    if (root.matches('.project-ctx-menu')) enhanceCtxMenu(root);
    if (root.matches('.ws-opt[data-path]')) enhanceWsOpt(root);
    root.querySelectorAll && root.querySelectorAll('.ws-opt[data-path]').forEach(enhanceWsOpt);
    if (root.matches('.ws-list-container')) groupWsList(root);
    root.querySelectorAll && root.querySelectorAll('.ws-list-container').forEach(groupWsList);
  }
  let detailQueued = false;
  const mo = new MutationObserver((muts) => {
    for (const m of muts) {
      for (const n of m.addedNodes) scan(n);
      if (!detailQueued && m.target && (m.target.id === 'workspaceDetailBody' || (m.target.closest && m.target.closest('#workspaceDetailBody')))) {
        detailQueued = true;
        requestAnimationFrame(() => { detailQueued = false; enhanceDetail(); });
      }
    }
  });
  function boot() {
    addHeaderButton();
    scan(document.body);
    enhanceDetail();
    mo.observe(document.body, { childList: true, subtree: true });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, { once: true });
  else boot();
})();
