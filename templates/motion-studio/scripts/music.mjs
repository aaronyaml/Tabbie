#!/usr/bin/env node
// Generate a film's soundtrack with ElevenLabs Music (the only music provider the studio uses).
//   npm run music -- --project <slug> --prompt "<what to make>" [--seconds 20] [--take take1] [--vocals]
// Writes public/audio/music/<slug>/source/<take>.mp3 (+ <take>.prompt.txt). Needs ELEVENLABS_API_KEY in
// this studio's .env (elevenlabs.io → Developers → API keys); it uses your ElevenLabs credits. The key is
// only ever sent to api.elevenlabs.io and never printed.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
try {
  for (const l of fs.readFileSync(path.join(root, ".env"), "utf8").split("\n")) {
    const m = l.match(/^\s*(?:export\s+)?([A-Z0-9_]+)\s*=\s*['"]?([^'"#\s]*)['"]?/);
    if (m && m[2] && !process.env[m[1]]) process.env[m[1]] = m[2];
  }
} catch (_) { /* no .env */ }
const args = process.argv.slice(2);
const opt = (k, d) => (args.includes(`--${k}`) ? args[args.indexOf(`--${k}`) + 1] : d);
const project = opt("project"), prompt = opt("prompt");
const seconds = Math.max(5, Math.min(300, +opt("seconds", 20)));
const take = String(opt("take", "take1")).replace(/[^\w.-]/g, "-");
if (!project || !prompt) {
  console.error('usage: npm run music -- --project <slug> --prompt "<text>" [--seconds 20] [--take take1] [--vocals]');
  process.exit(1);
}
if (!process.env.ELEVENLABS_API_KEY) {
  console.error(`ElevenLabs isn't linked: add ELEVENLABS_API_KEY=<your key> to ${path.join(root, ".env")} (elevenlabs.io → Developers → API keys).`);
  process.exit(2);
}
const res = await fetch("https://api.elevenlabs.io/v1/music?output_format=mp3_44100_192", {
  method: "POST",
  headers: { "xi-api-key": process.env.ELEVENLABS_API_KEY, "content-type": "application/json" },
  body: JSON.stringify({ prompt, music_length_ms: Math.round(seconds * 1000), model_id: "music_v1", force_instrumental: !args.includes("--vocals") }),
});
if (!res.ok) {
  console.error(`ElevenLabs answered ${res.status}: ${(await res.text()).slice(0, 400)}`);
  process.exit(1);
}
const dir = path.join(root, "public", "audio", "music", project, "source");
fs.mkdirSync(dir, { recursive: true });
const outFile = path.join(dir, `${take}.mp3`);
fs.writeFileSync(outFile, Buffer.from(await res.arrayBuffer()));
fs.writeFileSync(path.join(dir, `${take}.prompt.txt`), prompt + "\n");
console.log(`✓ ${path.relative(root, outFile)}`);
