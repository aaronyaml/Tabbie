#!/usr/bin/env node
// Export a film's edit map to audio/analysis/<slug>/EDIT_MAP.json, the file the Motion panel's timeline reads.
//   npm run editmap -- <slug> [--file src/components/<slug>/editMap.ts] [--out audio/analysis/<slug>/EDIT_MAP.json]
// The edit map is pure data (schema motion.edit-map/1, see CLAUDE.md): this bundles the TS module with
// esbuild (it comes with Remotion), finds the export with that schema, checks it and writes it.
import { build } from "esbuild";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const args = process.argv.slice(2);
const opt = (k, d) => (args.includes(`--${k}`) ? args[args.indexOf(`--${k}`) + 1] : d);
const project = args.find((a, i) => !a.startsWith("--") && !["--file", "--out"].includes(args[i - 1]));
if (!project) { console.error("usage: npm run editmap -- <slug> [--file path] [--out path]"); process.exit(1); }
const file = path.resolve(root, opt("file", `src/components/${project}/editMap.ts`));
const out = path.resolve(root, opt("out", `audio/analysis/${project}/EDIT_MAP.json`));

const tmp = fs.mkdtempSync(path.join(root, "node_modules", ".cache-editmap-"));
const bundle = path.join(tmp, "m.mjs");
await build({ entryPoints: [file], bundle: true, format: "esm", platform: "node", outfile: bundle, logLevel: "error",
  external: ["react", "react/*", "remotion", "@remotion/*"] });
const mod = await import(pathToFileURL(bundle).href);
fs.rmSync(tmp, { recursive: true, force: true });
const map = Object.values(mod).find((v) => v && typeof v === "object" && v.schema === "motion.edit-map/1");
if (!map) { console.error(`no motion.edit-map/1 export in ${path.relative(root, file)}`); process.exit(1); }

const lint = [];
const ids = new Set();
for (const e of map.events || []) {
  if (ids.has(e.id)) lint.push({ level: "error", id: e.id, msg: "duplicate event id" });
  ids.add(e.id);
  if (!e.anchor || typeof e.anchor.timeMs !== "number" || typeof e.anchor.frame !== "number") lint.push({ level: "error", id: e.id, msg: "anchor needs timeMs and frame" });
  else if (e.anchor.frame !== Math.floor((e.anchor.timeMs * map.fps) / 1000 + 0.5)) lint.push({ level: "error", id: e.id, msg: "anchor.frame isn't round-half-up of timeMs" });
  if (String(e.anchor && e.anchor.source).startsWith("manual")) lint.push({ level: "warn", id: e.id, msg: "manual anchor (not from the analysis)" });
  if (!e.note) lint.push({ level: "warn", id: e.id, msg: "no note: say what this moment communicates" });
}
const plain = JSON.parse(JSON.stringify({ ...map, lint }));
fs.mkdirSync(path.dirname(out), { recursive: true });
fs.writeFileSync(out, JSON.stringify(plain, null, 1) + "\n");
console.log(`${map.project}: ${(map.events || []).length} events @ ${map.fps} fps (${map.durationFrames} frames)`);
for (const l of lint) console.log(`${l.level.toUpperCase()} ${l.id ? `[${l.id}] ` : ""}${l.msg}`);
console.log(`→ ${path.relative(root, out)}`);
process.exit(lint.some((l) => l.level === "error") ? 1 : 0);
