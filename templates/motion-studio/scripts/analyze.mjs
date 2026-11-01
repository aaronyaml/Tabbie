#!/usr/bin/env node
// Audio analysis for the Motion studio: tempo, beats, downbeats, hits, drops and sections, in ms and frames.
//   npm run audio -- analyze <audio file> --project <slug> [--fps 30] [--out <path>]
// Writes audio/analysis/<slug>/analysis.json (schema motion.audio-analysis/1), which the Motion panel
// draws on its timeline and edit maps anchor to. No Python and no extra packages: it decodes with
// Remotion's bundled ffmpeg (or one on PATH), then a spectral-flux onset envelope, an autocorrelation
// tempo and dynamic-programming beat tracking. Lyrics aren't transcribed: "words" stays empty.
// (--stems and --ref are accepted and ignored, so job prompts written for bigger studios still run.)
import { spawnSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const args = process.argv.slice(2);
if (args[0] === "analyze") args.shift();
const opt = (k, d) => (args.includes(`--${k}`) ? args[args.indexOf(`--${k}`) + 1] : d);
const flags = new Set(["--project", "--fps", "--out", "--ref"]);
const file = args.find((a, i) => !a.startsWith("--") && !flags.has(args[i - 1]));
const project = opt("project");
const FPS = +opt("fps", 30);
if (!file || !project) {
  console.error("usage: npm run audio -- analyze <audio file> --project <slug> [--fps 30] [--out path]");
  process.exit(1);
}
const src = path.resolve(process.cwd(), file);
if (!fs.existsSync(src)) { console.error(`no such file: ${file}`); process.exit(1); }
const out = path.resolve(root, opt("out", `audio/analysis/${project}/analysis.json`));

// ── ffmpeg / ffprobe: Remotion's own build, else the one on PATH ─────────────────────────────────
const remotionBin = path.join(root, "node_modules", ".bin", "remotion");
function tool(name, argv, opts = {}) {
  const tries = fs.existsSync(remotionBin) ? [[remotionBin, [name, ...argv]], [name, argv]] : [[name, argv]];
  for (const [cmd, a] of tries) {
    const r = spawnSync(cmd, a, { maxBuffer: 2 ** 31 - 1, ...opts });
    if (!r.error && r.status === 0) return r.stdout;
  }
  throw new Error(`${name} failed on ${file} (run npm install in the studio first)`);
}
let probe = {};
try {
  const j = JSON.parse(tool("ffprobe", ["-v", "error", "-select_streams", "a:0", "-show_streams", "-of", "json", src]).toString());
  probe = (j.streams || [])[0] || {};
} catch (_) { /* decoding below still tells us the length */ }

const SR = 22050, N = 1024, HOP = 256, FR = SR / HOP; // ~86 analysis frames per second
const pcm = tool("ffmpeg", ["-v", "error", "-i", src, "-vn", "-ac", "1", "-ar", String(SR), "-f", "f32le", "pipe:1"]);
const x = new Float32Array(pcm.buffer, pcm.byteOffset, Math.floor(pcm.length / 4));
const durationMs = (x.length / SR) * 1000;

// ── STFT: onset envelope (all bands and lows), RMS, spectral centroid ────────────────────────────
function fft(re, im) {
  const n = re.length;
  for (let i = 1, j = 0; i < n; i++) {
    let bit = n >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) { [re[i], re[j]] = [re[j], re[i]]; [im[i], im[j]] = [im[j], im[i]]; }
  }
  for (let len = 2; len <= n; len <<= 1) {
    const a = (-2 * Math.PI) / len, wr = Math.cos(a), wi = Math.sin(a);
    for (let i = 0; i < n; i += len) {
      let cr = 1, ci = 0;
      for (let k = 0; k < len / 2; k++) {
        const p = i + k, q = p + len / 2;
        const tr = re[q] * cr - im[q] * ci, ti = re[q] * ci + im[q] * cr;
        re[q] = re[p] - tr; im[q] = im[p] - ti; re[p] += tr; im[p] += ti;
        const nr = cr * wr - ci * wi; ci = cr * wi + ci * wr; cr = nr;
      }
    }
  }
}
const nFrames = Math.max(1, Math.floor((x.length - N) / HOP) + 1);
const win = Float64Array.from({ length: N }, (_, i) => 0.5 - 0.5 * Math.cos((2 * Math.PI * i) / N));
const BINS = N / 2 + 1, LOW = Math.round((150 * N) / SR); // bins below ~150 Hz: kick and bass
const flux = new Float64Array(nFrames), low = new Float64Array(nFrames), rms = new Float64Array(nFrames), cent = new Float64Array(nFrames);
let prev = new Float64Array(BINS);
const re = new Float64Array(N), im = new Float64Array(N);
for (let f = 0; f < nFrames; f++) {
  let e = 0;
  for (let i = 0; i < N; i++) { const v = x[f * HOP + i] || 0; re[i] = v * win[i]; im[i] = 0; e += v * v; }
  rms[f] = Math.sqrt(e / N);
  fft(re, im);
  const cur = new Float64Array(BINS);
  let fl = 0, lo = 0, num = 0, den = 0;
  for (let k = 0; k < BINS; k++) {
    const m = Math.hypot(re[k], im[k]);
    cur[k] = Math.log1p(100 * m);
    const d = cur[k] - prev[k];
    if (d > 0) { fl += d; if (k <= LOW) lo += d; }
    num += m * ((k * SR) / N); den += m;
  }
  flux[f] = fl; low[f] = lo; cent[f] = den ? num / den : 0;
  prev = cur;
}
const pct = (arr, p) => { const s = Float64Array.from(arr).sort(); return s[Math.min(s.length - 1, Math.floor(p * (s.length - 1)))] || 0; };
const norm = (arr) => { const top = pct(arr, 0.98) || 1; return arr.map((v) => Math.min(1.5, v / top)); };
const env = norm(flux), lowEnv = norm(low);
const msOf = (f) => (f * HOP * 1000) / SR; // an analysis frame's start: onsets land where they begin
const frameOf = (ms) => Math.floor((ms * FPS) / 1000 + 0.5); // round-half-up, the studio's frame policy
const r2 = (v) => Math.round(v * 100) / 100, r3 = (v) => Math.round(v * 1000) / 1000;
const timing = (ms) => ({ ms: r2(ms), frame: frameOf(ms), subframe: r3((ms * FPS) / 1000 - frameOf(ms)) });

// ── tempo: weighted autocorrelation of the onset envelope, 60-200 bpm, prior around 120 ───────────
const mean = env.reduce((a, b) => a + b, 0) / env.length;
const ac = (lag) => { let s = 0; for (let i = lag; i < env.length; i++) s += (env[i] - mean) * (env[i - lag] - mean); return s / (env.length - lag); };
let best = { score: -Infinity, lag: 0 };
const acs = new Map();
for (let lag = Math.floor((60 * FR) / 200); lag <= Math.ceil((60 * FR) / 60); lag++) {
  const v = ac(lag); acs.set(lag, v);
  const bpm = (60 * FR) / lag, w = Math.exp(-0.5 * Math.log2(bpm / 120) ** 2);
  if (v * w > best.score) best = { score: v * w, lag };
}
const a0 = acs.get(best.lag - 1) ?? 0, a1 = acs.get(best.lag), a2 = acs.get(best.lag + 1) ?? 0;
const shift = a0 - 2 * a1 + a2 ? (0.5 * (a0 - a2)) / (a0 - 2 * a1 + a2) : 0;
const P = best.lag + Math.max(-0.5, Math.min(0.5, shift)); // beat period in analysis frames
const pulse = durationMs > 3000 && best.score > 0 && ac(0) > 0 && best.score / ac(0) > 0.04;
let bpm = pulse ? (60 * FR) / P : 0;

// ── beats: dynamic programming (Ellis 2007) over the onset envelope at that period ────────────────
let beatFrames = [];
if (pulse) {
  const n = env.length, score = new Float64Array(n), back = new Int32Array(n).fill(-1), T = 100;
  for (let t = 0; t < n; t++) {
    let bestS = 0, bestI = -1;
    for (let tau = t - Math.round(2 * P); tau <= t - Math.round(P / 2); tau++) {
      if (tau < 0) continue;
      const s = score[tau] - T * Math.log((t - tau) / P) ** 2;
      if (bestI < 0 || s > bestS) { bestS = s; bestI = tau; }
    }
    score[t] = env[t] + (bestI >= 0 ? bestS : 0);
    back[t] = bestI;
  }
  let t = n - 1;
  for (let i = Math.max(0, n - Math.round(P)); i < n; i++) if (score[i] > score[t]) t = i;
  for (; t >= 0; t = back[t]) beatFrames.unshift(t);
  // trim weak beats at the edges (silence before the music starts / after it ends)
  const floor = 0.5 * Math.sqrt(beatFrames.reduce((s, f) => s + env[f] ** 2, 0) / Math.max(1, beatFrames.length));
  while (beatFrames.length && env[beatFrames[0]] < floor && rms[beatFrames[0]] < 0.01) beatFrames.shift();
  while (beatFrames.length && env[beatFrames.at(-1)] < floor && rms[beatFrames.at(-1)] < 0.01) beatFrames.pop();
}
const ibis = beatFrames.slice(1).map((f, i) => f - beatFrames[i]);
const ibiMean = ibis.reduce((a, b) => a + b, 0) / Math.max(1, ibis.length);
const ibiSd = Math.sqrt(ibis.reduce((s, v) => s + (v - ibiMean) ** 2, 0) / Math.max(1, ibis.length));
if (ibis.length > 4) bpm = (60 * FR) / ibiMean; // the tracked grid is the truth
const stability = ibis.length ? Math.max(0, 1 - (ibiSd / ibiMean) * 4) : 0;

// ── downbeats (4/4): the phase whose beats carry the most low-end ─────────────────────────────────
const METER = 4;
const phaseScore = [0, 1, 2, 3].map((ph) => {
  const sel = beatFrames.filter((_, i) => i % METER === ph);
  return sel.reduce((s, f) => s + lowEnv[f] + 0.5 * env[f], 0) / Math.max(1, sel.length);
});
const order = [0, 1, 2, 3].sort((a, b) => phaseScore[b] - phaseScore[a]);
const phase = order[0];
const downConf = phaseScore[order[0]] ? (phaseScore[order[0]] - phaseScore[order[1]]) / phaseScore[order[0]] : 0;
const beatTop = Math.max(1e-9, ...beatFrames.map((f) => env[f]));
const beats = beatFrames.map((f, i) => ({
  i, ...timing(msOf(f)), strength: r3(Math.min(1, env[f] / beatTop)), downbeat: i % METER === phase,
  bar: Math.floor((i + ((METER - phase) % METER)) / METER), barBeat: ((i - phase + METER * 8) % METER) + 1,
}));
const beatMs = beats.map((b) => b.ms);
const nearestBeat = (ms) => { let b = null; for (const v of beatMs) if (b === null || Math.abs(v - ms) < Math.abs(b - ms)) b = v; return b; };

// ── hits: peaks of the onset envelope (bass hits, transients, heavy impacts) ──────────────────────
const events = [];
const counts = {};
const add = (type, ms, extra) => { counts[type] = (counts[type] || 0) + 1; const nb = nearestBeat(ms);
  events.push({ id: `${type}#${counts[type]}`, ...timing(ms), type, ...extra,
    onGrid: nb !== null && Math.abs(nb - ms) <= 50, gridOffsetMs: nb === null ? null : r2(ms - nb) }); };
const W = 3, LOOK = Math.round(0.5 * FR), MINGAP = Math.round(0.06 * FR);
let last = -Infinity;
for (let f = W; f < nFrames - W; f++) {
  const v = env[f];
  if (v < 0.35 || f - last < MINGAP) continue;
  let peak = true;
  for (let k = f - W; k <= f + W; k++) if (env[k] > v) { peak = false; break; }
  if (!peak) continue;
  let m = 0; for (let k = Math.max(0, f - LOOK); k < f; k++) m += env[k]; m /= Math.max(1, Math.min(f, LOOK));
  if (v < m + 0.15) continue;
  last = f;
  const before = Math.max(1e-6, rms.slice(Math.max(0, f - 10), f).reduce((a, b) => a + b, 0) / Math.max(1, Math.min(f, 10)));
  const after = rms.slice(f, f + 6).reduce((a, b) => a + b, 0) / 6;
  const lowShare = lowEnv[f] / Math.max(1e-6, env[f]);
  const strength = Math.min(1, v);
  const type = strength >= 0.85 && after / before > 2 ? "heavy_impact" : lowShare > 0.6 && lowEnv[f] > 0.4 ? "bass_hit" : "transient";
  add(type, msOf(f), { strength: r3(strength), confidence: r3(Math.min(1, 0.4 + strength * 0.6)),
    band: cent[f] < 400 ? "low" : cent[f] < 3000 ? "mid" : "high", centroidHz: Math.round(cent[f]), loudnessJumpDb: r2(20 * Math.log10(Math.max(1e-6, after) / before)) });
}
if (events.length > 400) { events.sort((a, b) => b.strength - a.strength); events.length = 400; events.sort((a, b) => a.ms - b.ms); }

// ── energy per bar (or per 2 s without a pulse) → drops and sections ─────────────────────────────
const barStarts = beats.length >= 8 ? beats.filter((b) => b.downbeat).map((b) => b.ms) : [];
const edges = barStarts.length >= 4 ? [0, ...barStarts.filter((ms) => ms > 1), durationMs]
  : Array.from({ length: Math.ceil(durationMs / 2000) + 1 }, (_, i) => Math.min(durationMs, i * 2000));
const seg = [];
for (let i = 0; i + 1 < edges.length; i++) {
  if (edges[i + 1] - edges[i] < 50) continue;
  const a = Math.floor((edges[i] / 1000) * FR), b = Math.max(a + 1, Math.floor((edges[i + 1] / 1000) * FR));
  let e = 0; for (let f = a; f < Math.min(b, nFrames); f++) e += rms[f] ** 2;
  seg.push({ startMs: edges[i], endMs: edges[i + 1], e: Math.sqrt(e / (b - a)) });
}
const eTop = Math.max(1e-9, ...seg.map((s) => s.e));
seg.forEach((s) => { s.n = s.e / eTop; });
const avg = (l) => (l.length ? l.reduce((a, s) => a + s.n, 0) / l.length : 0);
let drops = [];
for (let i = 1; i + 1 < seg.length; i++) { // two segments after it, or it's just the ending
  const pre = avg(seg.slice(Math.max(0, i - 2), i)), post = avg(seg.slice(i, i + 2));
  if (post >= 0.6 && post >= pre * 1.6) drops.push({ i, rise: post - pre });
}
drops = drops.sort((a, b) => b.rise - a.rise).filter((d, k, l) => l.slice(0, k).every((o) => Math.abs(o.i - d.i) >= 8)).slice(0, 3).sort((a, b) => a.i - b.i);
drops.forEach((d) => add("drop", seg[d.i].startMs, { strength: r3(Math.min(1, d.rise * 1.5)), confidence: 0.6 }));
events.sort((a, b) => a.ms - b.ms);

// sections: cut where the energy steps (or at a drop), at least 4 bars (8 s without bars) long
const minLen = 4; // segments: bars, or 2 s windows
const cuts = [0];
for (let i = 1; i < seg.length; i++) {
  const isDrop = drops.some((d) => d.i === i);
  const step = Math.abs(avg(seg.slice(i, i + 2)) - avg(seg.slice(Math.max(0, i - 2), i)));
  if ((isDrop || step > 0.25) && i - cuts.at(-1) >= (isDrop ? 2 : minLen)) cuts.push(i);
}
const sections = [];
cuts.forEach((c, k) => {
  const part = seg.slice(c, cuts[k + 1] ?? seg.length), energy = avg(part);
  const isDrop = drops.some((d) => d.i === c);
  const nextDrop = drops.some((d) => d.i === cuts[k + 1]);
  const type = isDrop ? "drop" : k === 0 && energy < 0.5 ? "intro" : k === cuts.length - 1 && energy < 0.5 && cuts.length > 1 ? "outro"
    : nextDrop ? "build" : energy < 0.4 ? "breakdown" : "main";
  const startMs = part[0].startMs, endMs = part.at(-1).endMs;
  const prevS = sections.at(-1);
  if (prevS && prevS.type === type && type !== "drop") { // one "main" from two steps of the same kind
    prevS.energy = r3((prevS.energy * (prevS.endMs - prevS.startMs) + energy * (endMs - startMs)) / (endMs - prevS.startMs));
    Object.assign(prevS, { endMs: r2(endMs), endFrame: frameOf(endMs) });
    return;
  }
  sections.push({ type, startMs: r2(startMs), endMs: r2(endMs), startFrame: frameOf(startMs), endFrame: frameOf(endMs), energy: r3(energy), confidence: 0.6 });
});

// ── write ─────────────────────────────────────────────────────────────────────────────────────────
const sha256 = crypto.createHash("sha256").update(fs.readFileSync(src)).digest("hex");
const rel = (p) => path.relative(root, p).split(path.sep).join("/");
const sr = +probe.sample_rate || null;
const analysis = {
  schema: "motion.audio-analysis/1", project, fps: FPS,
  framePolicy: "round-half-up: frame = floor(ms * fps / 1000 + 0.5)",
  source: { path: rel(src), sha256, sampleRate: sr, channels: +probe.channels || null, codec: probe.codec_name || null,
    samples: sr ? Math.round((durationMs / 1000) * sr) : null },
  durationMs: r2(durationMs), durationFrames: Math.round((durationMs * FPS) / 1000),
  tempo: { bpm: r2(bpm), beatPeriodMs: bpm ? r2(60000 / bpm) : null, stability: r3(stability), meter: METER,
    downbeatPhase: phase, downbeatConfidence: r3(downConf), alternatives: bpm ? [r2(bpm / 2), r2(bpm * 2)] : [] },
  wordsSource: null, words: [], audioTags: [], beats, events, sections, references: [], sounds: [],
  tools: { analyzer: "tabbie-motion-analyze/1" },
};
fs.mkdirSync(path.dirname(out), { recursive: true });
fs.writeFileSync(out, JSON.stringify(analysis, null, 1) + "\n");
const by = (t) => events.filter((e) => e.type === t).length;
console.log(`${project}: ${(durationMs / 1000).toFixed(2)} s · ${bpm ? `${bpm.toFixed(1)} bpm (stability ${stability.toFixed(2)}, downbeat phase ${phase}, confidence ${downConf.toFixed(2)})` : "no steady pulse"}`);
console.log(`  ${beats.length} beats, ${beats.filter((b) => b.downbeat).length} downbeats · hits: ${by("heavy_impact")} heavy, ${by("bass_hit")} bass, ${by("transient")} transient · drops: ${drops.map((d) => (seg[d.i].startMs / 1000).toFixed(2) + " s").join(", ") || "none"}`);
console.log(`  sections: ${sections.map((s) => `${s.type} ${(s.startMs / 1000).toFixed(1)}–${(s.endMs / 1000).toFixed(1)} s`).join(" · ")}`);
console.log(`→ ${rel(out)}`);
