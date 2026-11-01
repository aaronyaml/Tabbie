#!/usr/bin/env node
// Hermes WebUI — Hugeicons build. Run: node build-icons.mjs
// Emits hugeicons.js (icon data + hugeicons.runtime.js) and hugeicons.css.
// Needs @hugeicons/core-free-icons in .build/ (gitignored):
//   cd .build && npm i @hugeicons/core-free-icons@^4.2.3
// Reads the upstream WebUI sources (read-only) to fingerprint every inline
// Lucide <svg>, so the runtime can swap each one for its Hugeicon.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const STATIC = path.resolve(HERE, '../hermes-webui/static');
const PKG = path.join(HERE, '.build/node_modules/@hugeicons/core-free-icons/dist/esm/index.js');
const H = await import(pathToFileURL(PKG).href);

// ── Lucide name (li() keys + inline-only glyphs) → Hugeicon ────────────────
// Names follow common app-icon concepts.
const LUCIDE = {
  'message-square': 'BubbleChatIcon', calendar: 'Calendar03Icon', layers: 'Layers01Icon',
  lightbulb: 'BulbIcon', folder: 'Folder01Icon', 'folder-plus': 'FolderAddIcon', link: 'Link01Icon',
  'external-link': 'LinkSquare02Icon', 'list-todo': 'CheckListIcon', pencil: 'Edit02Icon',
  save: 'FloppyDiskIcon', 'chevron-down': 'ArrowDown01Icon', 'chevron-right': 'ArrowRight01Icon',
  'chevron-up': 'ArrowUp01Icon', 'chevron-left': 'ArrowLeft01Icon', download: 'Download04Icon',
  upload: 'Upload04Icon', braces: 'CodeIcon', 'trash-2': 'Delete02Icon', archive: 'ArchiveIcon',
  unarchive: 'Archive02Icon', settings: 'Settings02Icon', 'alert-triangle': 'Alert02Icon',
  'refresh-cw': 'RefreshIcon', 'rotate-cw': 'RefreshIcon', undo: 'Undo02Icon', check: 'Tick02Icon',
  lock: 'LockPasswordIcon', star: 'StarIcon', pin: 'PinIcon', bookmark: 'Bookmark01Icon',
  x: 'Cancel01Icon', square: 'StopIcon', plus: 'Add01Icon', 'arrow-up': 'ArrowUp02Icon',
  'arrow-right': 'ArrowRight02Icon', 'arrow-up-right': 'ArrowUpRight01Icon', loader: 'Loading03Icon',
  pause: 'PauseIcon', terminal: 'ComputerTerminal01Icon', 'file-text': 'File01Icon',
  'file-pen': 'FileEditIcon', search: 'Search01Icon', globe: 'Globe02Icon', play: 'PlayIcon',
  'skip-forward': 'NextIcon', wrench: 'Wrench01Icon', plug: 'Plug01Icon', brain: 'Brain01Icon',
  sparkles: 'SparklesIcon', 'book-open': 'BookOpen01Icon', book: 'Book02Icon',
  'grip-vertical': 'DragDropVerticalIcon', clock: 'Clock01Icon', bot: 'Robot01Icon', eye: 'EyeIcon',
  shuffle: 'ShuffleIcon', paperclip: 'Attachment01Icon', copy: 'Copy01Icon',
  'rotate-ccw': 'RotateLeft01Icon', user: 'UserCircleIcon', image: 'Image01Icon',
  'file-code': 'FileScriptIcon', zap: 'FlashIcon', 'clipboard-list': 'ClipboardIcon', map: 'MapsIcon',
  'git-branch': 'GitBranchIcon', 'volume-2': 'VolumeHighIcon', 'audio-lines': 'VoiceIcon',
  hash: 'HashtagIcon', cpu: 'CpuIcon', 'dollar-sign': 'DollarCircleIcon', menu: 'Menu01Icon',
  kanban: 'KanbanIcon', 'bar-chart': 'Analytics01Icon', layout: 'DashboardSquare02Icon',
  sun: 'Sun03Icon', moon: 'Moon02Icon', monitor: 'ComputerIcon', sliders: 'SlidersHorizontalIcon',
  key: 'Key01Icon', server: 'ServerStack01Icon', help: 'HelpCircleIcon', mic: 'Mic01Icon',
  camera: 'Camera01Icon', 'apps-add': 'DashboardSquareAddIcon', share: 'Share08Icon',
  'link-off': 'Unlink02Icon', bug: 'Bug01Icon', 'more-vertical': 'MoreVerticalIcon',
  'scan-text': 'ScanIcon', 'zoom-in': 'ZoomInAreaIcon', 'zoom-out': 'ZoomOutAreaIcon',
  maximize: 'ArrowExpand01Icon', minimize: 'ArrowShrink01Icon', wrap: 'TextWrapIcon',
  compass: 'Compass01Icon', send: 'SentIcon',
};

// ── inline <svg> fingerprints → Lucide name (first matching substring wins) ─
const INLINE = [
  ['path:M20 21v-2a4 4 0 0 0-4-4H8', 'user'],
  ['line:3,6,21,6|line:3,12,21,12|line:3,18,21,18', 'menu'],
  ['path:M20.49 15a9 9 0 1 1-2.12-9.36L23 10', 'rotate-cw'],
  ['path:M21 12a9 9 0 1 1-6.219-8.56', 'rotate-cw'],
  ['path:M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8', 'refresh-cw'],
  ['polyline:1 4 1 10 7 10', 'rotate-ccw'],
  ['path:M6 9V4H1', 'rotate-ccw'],
  ['rect:3,4,18,16,2|path:M8 4v16', 'kanban'],
  ['path:M18 20V10|path:M12 20V4', 'bar-chart'],
  ['rect:3,3,18,18,2|path:M3 9h18|path:M9 21V9', 'layout'],
  ['path:M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z|path:M14 2v6h6|path:M8 13h8', 'file-text'],
  ['path:M9 13l-2 2 2 2', 'file-code'],
  ['circle:12,12,3|path:M19.4 15a1.65', 'settings'],
  ['circle:11,11,8|path:M21 21l-4.35-4.35', 'search'],
  ['path:M12 1v2M12 21v2', 'sun'],
  ['line:4,21,4,14|line:4,10,4,3', 'sliders'],
  ['path:M21 2l-2 2m-7.61 7.61', 'key'],
  ['path:M12 2l3 7h7l-5.5 4.3', 'star'],
  ['polygon:8,1.5 9.8,5.8', 'star'],
  ['polygon:8,2 9.8,6.2', 'star'],
  ['path:M9 2v6|path:M15 2v6|path:M7 8h10v4', 'plug'],
  ['rect:2,3,20,8,2|rect:2,13,20,8,2', 'server'],
  ['path:M9.09 9a3 3 0 0 1 5.83 1', 'help'],
  ['path:M9.09 9a3 3 0 1 1 5.82 1', 'help'],
  ['path:M21.44 11.05l-9.19 9.19', 'paperclip'],
  ['rect:9,1,6,12,3', 'mic'],
  ['rect:4,4,16,16,2|rect:9,9,6,6', 'cpu'],
  ['path:M9.5 2A2.5 2.5 0 0 1 12 4.5v15', 'brain'],
  ['rect:3,6,18,15,2|path:m7 6 2-3h6l2 3', 'camera'],
  ['path:M3 7V5a2 2 0 0 1 2-2h5l2 3h7', 'folder'],
  ['path:M2 4.5h4l1.5 1.5H14v7H2z', 'folder'],
  ['path:M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9', 'folder-plus'],
  ['path:M12 20h9|path:M16.5 3.5', 'pencil'],
  ['path:M11.5 2.5l2 2L5 13H3v-2z', 'pencil'],
  ['path:M3 6h18|path:M19 6v14', 'trash-2'],
  ['path:M3.5 4.5h9M6.5 4.5V3h3v1.5', 'trash-2'],
  ['rect:6,4,4,16|rect:14,4,4,16', 'pause'],
  ['polygon:5 3 19 12 5 21 5 3|line:22,4,22,20', 'skip-forward'],
  ['path:M21 4v16|path:M6.029 4.285', 'skip-forward'],
  ['rect:3,3,7,7|rect:14,3,7,7|rect:3,14,7,7', 'apps-add'],
  ['path:M13 2L3 14h7l-1 8 10-12h-7l1-8z', 'zap'],
  ['circle:18,5,3|circle:6,12,3|circle:18,19,3', 'share'],
  ['path:M18 8a6 6 0 0 0-8.49 0L6 11.5', 'link-off'],
  ['path:M21 12.79A9 9 0 1111.21 3', 'moon'],
  ['rect:2,3,20,14,2|path:M8 21h8M12 17v4', 'monitor'],
  ['path:M4 19.5A2.5 2.5 0 0 1 6.5 17H20', 'book'],
  ['path:M7 17 17 7|path:M7 7h10v10', 'arrow-up-right'],
  ['path:m8 2 1.88 1.88', 'bug'],
  ['polyline:15 18 9 12 15 6', 'chevron-left'],
  ['path:M6 9l6 6 6-6', 'chevron-down'],
  ['circle:12,5,1.5|circle:12,12,1.5|circle:12,19,1.5', 'more-vertical'],
  ['circle:8,3,1.25|circle:8,8,1.25|circle:8,13,1.25', 'more-vertical'],
  ['path:M8 3H5a2 2 0 0 0-2 2v3|path:M16 3h3', 'scan-text'],
  ['rect:4,4,8,8,1.5', 'square'],
  ['rect:5,5,14,14,2', 'square'],
  ['rect:1.5,2,13,3,1|path:M2.5 5v8h11V5|line:6,8.5,10,8.5', 'archive'],
  ['rect:1.5,2,13,3,1|path:M2.5 5v8h11V5|polyline:6.5,7 8,5.5 9.5,7', 'unarchive'],
  ['rect:4.5,4.5,8.5,8.5,1.5|path:M3 11.5V3h8.5', 'copy'],
  ['path:M8 1.8l1.1 3.1', 'sparkles'],
  ['path:M6.7 9.3a3 3 0 0 1 0-4.2', 'link'],
  ['path:M14 10.5v2.5a1 1 0 0 1-1 1H3', 'download'],
  ['path:M18 6 6 18|path:m6 6 12 12', 'x'],
  ['circle:10,10,6|path:M10 7v6M7 10h6', 'zoom-in'],
  ['circle:10,10,6|path:M7 10h6', 'zoom-out'],
  ['path:M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5|path:M4 4l5 5', 'minimize'],
  ['path:M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5', 'maximize'],
  ['path:M16 5H3|path:M16 12H3|path:M9 19H3', 'wrap'],
  ['circle:12,12,10|path:m16.24 7.76', 'compass'],
];
// Not icons (brand marks, context ring, charts): left alone on purpose.
const IGNORE = ['circle:12,12,9.75|circle:12,12,9.75'];

// ── emoji / glyph icons in the UI chrome → Hugeicon (unlisted ones are removed)
const GLYPH = {
  '⚡': 'FlashIcon', '▶': 'PlayIcon', '📋': 'ClipboardIcon', '🖼': 'Image01Icon', '⏳': 'HourglassIcon',
  '⌛': 'HourglassIcon', '📎': 'Attachment01Icon', '🧠': 'Brain01Icon', '🔧': 'Wrench01Icon',
  '👤': 'UserCircleIcon', '🌍': 'Globe02Icon', '🌐': 'Globe02Icon', '✅': 'CheckmarkCircle02Icon',
  '☐': 'SquareIcon', '⚠': 'Alert02Icon', '✓': 'Tick02Icon', '✔': 'Tick02Icon', '▸': 'ArrowRight01Icon',
  '▾': 'ArrowDown01Icon', '▴': 'ArrowUp01Icon', '▲': 'ArrowUp01Icon', '▼': 'ArrowDown01Icon',
  '📄': 'File01Icon', '🎵': 'MusicNote01Icon', '🎬': 'Video01Icon', '🤖': 'Robot01Icon',
  '💬': 'BubbleChatIcon', '✕': 'Cancel01Icon', '✖': 'Cancel01Icon', '❌': 'CancelCircleIcon',
  '⚙': 'Settings02Icon', '🎮': 'GameController01Icon', '📝': 'Note01Icon', '📜': 'Note01Icon',
  '🔌': 'Plug01Icon', '📡': 'Wifi01Icon', '🔑': 'Key01Icon', '⏹': 'StopIcon', '🖥': 'ComputerIcon',
  '⏸': 'PauseIcon', '↻': 'RefreshIcon', '★': 'StarIcon', '⭐': 'StarIcon', '↗': 'ArrowUpRight01Icon',
  '↩': 'ArrowTurnBackwardIcon', '🔒': 'LockPasswordIcon', '🔍': 'Search01Icon', '📁': 'Folder01Icon',
  '📂': 'Folder01Icon', '🗑': 'Delete02Icon', '✏': 'Edit02Icon', '🔄': 'RefreshIcon', 'ℹ': 'InformationCircleIcon',
  '🔔': 'Notification01Icon', '📅': 'Calendar03Icon', '🕐': 'Clock01Icon', '⏰': 'Clock01Icon',
  '💡': 'BulbIcon', '🚀': 'Rocket01Icon', '✨': 'SparklesIcon', '✦': 'SparklesIcon', '🛑': 'StopIcon',
};
// Extras the runtime / CSS reference directly.
const EXTRA = ['PanelLeftIcon', 'PanelLeftCloseIcon', 'FileImageIcon', 'FileVideoIcon', 'FileAudioIcon', 'Pdf02Icon', 'FileZipIcon', 'FileCodeIcon', 'FileTextIcon', 'FileSpreadsheetIcon', 'ArrowUpDownIcon', 'InformationCircleIcon', 'CheckmarkCircle02Icon', 'CircleIcon', 'Flag02Icon', 'KanbanIcon', 'CheckListIcon', 'Calendar03Icon', 'UserMultiple02Icon', 'BubbleChatQuestionIcon', 'AiBrain01Icon', 'AiMagicIcon'];

// ── fingerprint: element tags + geometry, document order (mirrored in runtime) ─
const GEO = ['d', 'points', 'x1', 'y1', 'x2', 'y2', 'cx', 'cy', 'r', 'x', 'y', 'width', 'height', 'rx', 'ry'];
const NOPE = ['title', 'defs', 'lineargradient', 'radialgradient', 'stop', 'text', 'svg', 'use', 'mask', 'clippath', 'pattern', 'image', 'foreignobject'];
function fingerprint(inner) {
  inner = inner.replace(/<!--[\s\S]*?-->/g, '');
  const out = [];
  for (const m of inner.matchAll(/<([a-zA-Z]+)((?:\s+[\w:-]+\s*=\s*(?:"[^"]*"|'[^']*'))*)\s*\/?>/g)) {
    const tag = m[1].toLowerCase();
    if (tag === 'g') continue;
    if (NOPE.includes(tag)) return null;
    const a = {};
    for (const am of m[2].matchAll(/([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')/g)) a[am[1]] = am[2] ?? am[3];
    out.push(tag + ':' + GEO.filter((k) => k in a).map((k) => a[k].replace(/\s+/g, ' ').trim()).join(','));
  }
  return out.length ? out.join('|') : null;
}
function hash(s) { let h = 5381; for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0; return (h >>> 0).toString(36); }

// ── render one Hugeicon faithfully (as @hugeicons/react-native does) ──────
// The renderer forces stroke=currentColor + strokeWidth on every child, so both
// inherit from our <svg>. Children without a cap/join render butt/miter there,
// so say so explicitly (our root defaults to round).
const kebab = (k) => k.replace(/[A-Z]/g, (c) => '-' + c.toLowerCase());
function render(name) {
  const icon = H[name];
  if (!Array.isArray(icon)) throw new Error('No Hugeicon ' + name);
  return icon.map(([tag, attrs]) => {
    const a = { ...attrs };
    delete a.key; delete a.stroke; delete a.strokeWidth;
    if (a.strokeLinecap === 'round') delete a.strokeLinecap; else a.strokeLinecap ??= 'butt';
    if (a.strokeLinejoin === 'round') delete a.strokeLinejoin; else a.strokeLinejoin ??= 'miter';
    return `<${tag}${Object.entries(a).map(([k, v]) => ` ${kebab(k)}="${v}"`).join('')}/>`;
  }).join('');
}

// ── scan sources ──────────────────────────────────────────────────────────
const sources = fs.readdirSync(STATIC).filter((f) => /\.(js|html)$/.test(f) && f !== 'i18n.js').map((f) => path.join(STATIC, f));
sources.push(path.join(HERE, 'spaces-projects.js'));
const liSrc = fs.readFileSync(path.join(STATIC, 'icons.js'), 'utf8');
const FP = {}; const unmapped = new Map();
const add = (fp, lucide, where) => {
  const hi = LUCIDE[lucide];
  if (!hi) throw new Error(`No Hugeicon for lucide "${lucide}" (${where})`);
  const k = hash(fp);
  if (FP[k] && FP[k] !== hi) console.warn(`fingerprint clash ${k}: ${FP[k]} vs ${hi}`);
  FP[k] = hi;
};
for (const m of liSrc.matchAll(/^\s*'([a-z0-9-]+)':\s*'(.*)',\s*$/gm)) {
  if (!LUCIDE[m[1]]) throw new Error('li() icon without a mapping: ' + m[1]);
  add(fingerprint(m[2]), m[1], 'icons.js');
}
for (const [k, v] of Object.entries(spLocal())) add(fingerprint(v), k, 'spaces-projects.js');
let inlineCount = 0;
for (const file of sources) {
  const t = fs.readFileSync(file, 'utf8');
  for (const m of t.matchAll(/<svg\b[^>]*>([\s\S]*?)<\/svg>/g)) {
    if (m[1].includes('${')) continue;
    const fp = fingerprint(m[1]);
    if (!fp || IGNORE.includes(fp)) continue;
    inlineCount++;
    if (FP[hash(fp)]) continue;
    const rule = INLINE.find(([s]) => fp.includes(s));
    if (rule) add(fp, rule[1], path.basename(file));
    else unmapped.set(fp, path.basename(file));
  }
}
function spLocal() {
  const t = fs.readFileSync(path.join(HERE, 'spaces-projects.js'), 'utf8');
  const o = {};
  const block = t.match(/const LOCAL_ICONS = \{([\s\S]*?)\n\s*\};/);
  if (block) for (const m of block[1].matchAll(/^\s*'?([\w-]+)'?:\s*'(.*)',?\s*$/gm)) o[m[1]] = m[2];
  return o;
}

// ── emit ──────────────────────────────────────────────────────────────────
const names = new Set([...Object.values(LUCIDE), ...Object.values(GLYPH), ...EXTRA]);
const P = {};
for (const n of [...names].sort()) P[n] = render(n);
const LI = {};
for (const m of liSrc.matchAll(/^\s*'([a-z0-9-]+)':/gm)) LI[m[1]] = LUCIDE[m[1]];
for (const k of Object.keys(spLocal())) LI[k] = LUCIDE[k];

const runtime = fs.readFileSync(path.join(HERE, 'hugeicons.runtime.js'), 'utf8');
const data = [
  `const P = ${JSON.stringify(P, null, 0).replace(/","/g, '",\n"')};`,
  `const LI = ${JSON.stringify(LI)};`,
  `const FP = ${JSON.stringify(FP)};`,
  `const GLYPH = ${JSON.stringify(GLYPH)};`,
].join('\n');
fs.writeFileSync(path.join(HERE, 'hugeicons.js'),
  `/* Hermes — Hugeicons (free, stroke-rounded) for the WebUI. GENERATED by build-icons.mjs; edit\n * hugeicons.runtime.js or the build maps, then rebuild. Icon data © Hugeicons (MIT). */\n`
  + runtime.replace('/*@DATA@*/', data));

// CSS-drawn glyphs → Hugeicon masks / backgrounds
const uri = (n, stroke = 'black', w = 1.5) => `url("data:image/svg+xml,${encodeURIComponent(
  `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="${stroke}" stroke-width="${w}" stroke-linecap="round" stroke-linejoin="round">${render(n)}</svg>`).replace(/'/g, '%27')}")`;
const mask = (n, size) => `content: "" !important; display: inline-block; width: ${size}px; height: ${size}px; background: currentColor !important; -webkit-mask: ${uri(n)} center / contain no-repeat; mask: ${uri(n)} center / contain no-repeat;`;
const css = `/* Hermes — Hugeicons styling. GENERATED by build-icons.mjs (do not edit by hand).
 * Icon tokens: stroke 1.5, 2 only for active/selected; currentColor. */
svg[data-hi] { stroke: currentColor; stroke-width: 1.5 !important; overflow: visible; }
.nav-tab.active svg[data-hi], .rail-btn.active svg[data-hi], .active > svg[data-hi],
[aria-selected="true"] svg[data-hi], [aria-current="page"] svg[data-hi], [aria-pressed="true"] > svg[data-hi] { stroke-width: 2 !important; }
.hx-glyph { display: inline-flex; align-items: center; vertical-align: -0.15em; line-height: 1; }
.hx-glyph > svg { width: 1.1em; height: 1.1em; }

/* CSS-drawn glyphs */
.cron-paused-summary::before { ${mask('ArrowRight01Icon', 12)} vertical-align: -2px; }
.model-group.collapsible:before { ${mask('ArrowRight01Icon', 10)} vertical-align: -1px; }
.model-group.sub.collapsible:before { ${mask('ArrowRight01Icon', 9)} vertical-align: -1px; }
.markdown-table-sort-indicator::before { ${mask('ArrowUpDownIcon', 10)} }
.msg-body th[aria-sort="ascending"] .markdown-table-sort-indicator::before { ${mask('ArrowUp01Icon', 10)} }
.msg-body th[aria-sort="descending"] .markdown-table-sort-indicator::before { ${mask('ArrowDown01Icon', 10)} }
@media (max-width:560px) {
  .selected-text-reply-btn::before { ${mask('ArrowTurnBackwardIcon', 16)} }
  .selected-text-refine-btn::before { ${mask('SparklesIcon', 16)} }
}
select, .project-bindings-combo-trigger { background-image: ${uri('ArrowDown01Icon', '#8888aa')} !important; background-size: 14px 14px !important; }
.kanban-modal-row select { background-image: ${uri('ArrowDown01Icon', '#90a0b8')} !important; background-size: 16px !important; }
.kanban-modal-row select:focus { background-image: ${uri('ArrowDown01Icon', '#FFD700')} !important; }

/* apple-skin phone composer: "+" attach, workspace folder, pill caret */
@media (max-width: 640px) {
  #btnAttach::before { ${mask('Add01Icon', 20)} position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%) !important; border-radius: 0 !important; }
  .composer-mobile-config-btn .hx-ws-label::before { ${mask('Folder01Icon', 16)} margin-right: 6px; vertical-align: -3px; }
  .composer-mobile-config-btn .hx-ws-label::after { ${mask('ArrowDown01Icon', 14)} margin-left: 4px; vertical-align: -2px; opacity: .7; }
}
`;
fs.writeFileSync(path.join(HERE, 'hugeicons.css'), css);

console.log(`icons: ${Object.keys(P).length} Hugeicons, ${Object.keys(LI).length} li() names, ${Object.keys(FP).length} fingerprints, ${inlineCount} inline svgs scanned`);
if (unmapped.size) { console.log('UNMAPPED inline svgs:'); for (const [fp, f] of unmapped) console.log(' ', f, fp.slice(0, 140)); }
console.log('hugeicons.js', fs.statSync(path.join(HERE, 'hugeicons.js')).size, 'bytes; hugeicons.css', css.length, 'bytes');
