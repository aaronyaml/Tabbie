# Motion studio

A Remotion project (React → video) where Tabbie's Motion panel and `/motion` jobs make films.
Tabbie created this folder from its template; it's yours to change. Style: `MOTION_RULES.md`, every time.

## Rules
- 1920×1080 at 30 fps unless the brief says otherwise. Animation is a pure function of the frame
  (`useCurrentFrame()`, `interpolate`, `spring`): no timers, no `Math.random()` without a seed, no CSS transitions.
- **Every film is new.** Build it in its own folders; never open, copy or adapt another film's files.
- The product's repo (named in the job as PRODUCT SOURCE) is read-only: take its name, colours, logo, fonts,
  real screens and copy from it, and copy needed assets into `public/assets/<product>/`.
- Music timing comes from the analysis, never from guessing: every synced moment anchors to an id in
  `audio/analysis/<slug>/analysis.json`.
- Long steps one at a time (renders are heavy). Keep `output/` for finished films; delete scratch renders.

## A film's files (slug = the film's id, e.g. `acme-launch`)
| Path | What |
|---|---|
| `studio/<slug>/project.json` | the panel's state (`tabbie.motion-studio/1`): step, brief, audio, mapping, jobs. Change only the fields a job asks for |
| `studio/<slug>/keypoints.json`, `revisions.json` | written by the panel: moments the user marked, parts to redo |
| `studio/<slug>/storyboard.json`, `render.json` | written by jobs (schemas are in each job's message) |
| `studio/<slug>/jobs/<id>.json` | a job's result: `{"status":"done"\|"failed","note":"<one line>"}`. Always write it last |
| `public/audio/music/<slug>/` | the track: `source/` (original, never modified) and `edit.wav` (the trimmed cut that's analysed and mixed) |
| `audio/analysis/<slug>/analysis.json` | `npm run audio -- analyze <file> --project <slug> --fps 30` |
| `audio/analysis/<slug>/EDIT_MAP.json` | `npm run editmap -- <slug>` from `src/components/<slug>/editMap.ts` |
| `src/compositions/<slug>/`, `src/components/<slug>/` | the film's code. Register it in `src/Root.tsx` with `id="<slug>"` |
| `output/<slug>.mp4` | the render |

## Commands
- `npm run studio`: Remotion's preview app. `npm run render -- <slug> output/<slug>.mp4`: render a film.
- `npm run ffmpeg -- …`, `npm run ffprobe -- …`: Remotion's own ffmpeg (use these when `ffmpeg` isn't on PATH).
  Cut audio sample-accurately: decode, then `-af atrim=start=…:end=…,asetpts=PTS-STARTPTS`; never `-ss` before `-i`.
- `npm run audio -- analyze <file> --project <slug> --fps 30`: tempo, beats (`downbeat` marks bar starts),
  `events` (`bass_hit`, `transient`, `heavy_impact`, `drop`) and `sections`, each in ms and frame
  (frame = floor(ms × fps / 1000 + 0.5)). It doesn't transcribe lyrics: `words` is empty.
- `npm run music -- --project <slug> --prompt "…" --seconds 20`: ElevenLabs Music → `public/audio/music/<slug>/source/take1.mp3`.
  Needs `ELEVENLABS_API_KEY` in `.env`; if it's missing, stop and say so (the panel tells the user how to link it).
- `npm run editmap -- <slug>`: export and lint the edit map.

## Sound effects (ready to use, no ElevenLabs needed)
`public/audio/sfx/` holds Tabbie's sound-effects library: 110 sounds (whooshes, UI clicks and taps, pops,
impacts, risers, bass hits, digital blips, shimmers, tails, ambient pads and beds, textures, foley), most
with 2-3 variants. **Reuse these before making anything new.** `public/audio/sfx/manifest.json` lists every
sound: `category`, `files` (`<name>_<n>.mp3`), `durations` (s) and `prompt` (what it sounds like): search it.
```tsx
import { Sfx, SfxTrack, SfxBus, AmbientBed, cue } from "../../audio";
<Sfx name="whoosh_soft" atSec={1.2} />                      // one hit; variant={2} for another take
<SfxTrack cues={[cue("pop_soft", 42), cue("impact_low", 90, { gainDb: -3 })]} />
<SfxBus gains={{ whoosh: -9 }}>…</SfxBus>                   // per-category dB, defaults in src/audio/library.ts
<AmbientBed name="ambient_pad" />                           // looping bed with fades
```
- Mix them under the music: most hits sit 10-20 dB below it. A whoosh within ~0.3 s of a sung or spoken word
  reads as an extra syllable; move it. Audition variants; don't stack two whooshes on one cut.
- Only when nothing in the library fits **and** `ELEVENLABS_API_KEY` is in `.env`: make a new one with
  ElevenLabs' sound-generation API, save it as `public/audio/sfx/<new_name>_1.mp3`, and add its entry to
  `manifest.json` (category, files, durations, prompt). Never overwrite or rename a library sound. No key:
  use the closest library sound and say so.

## Edit map (`src/components/<slug>/editMap.ts`, schema `motion.edit-map/1`)
Export one object; the composition reads the same object for its timing.
```ts
export const editMap = {
  schema: "motion.edit-map/1", project: "<slug>", fps: 30, durationFrames: 900, musicOffsetFrames: 0,
  analysis: { path: "public/audio/music/<slug>/edit.wav", sha256: "<analysis.source.sha256>" },
  events: [{
    id: "hook", treatment: "dropTransition", priority: 1, intensity: 0.8,
    anchor: { source: "event:drop#1", timeMs: 8123.4, frame: 244, subframe: -0.3, strength: 0.9, confidence: 0.6 },
    actions: [{ type: "cut" }, { type: "flash" }], note: "the product appears: the first big hit",
  }],
} as const;
```
- Treatments: `impactHit`, `slashHit`, `bassPunch`, `lyricAccent`, `dropTransition` (once), `beatCut`, `speedRampHit`, `custom`.
- Anchor sources (also what the panel's keypoints use): `beat:<i>`, `downbeat:<n>` (1-based), `<event id>`
  (e.g. `bass_hit#3`, `drop#1`), `section:<type>#<n>`, `word[<i>]:<text>`, or `manual` (only when nothing fits; say why in `note`).
- Pick the moments that matter; don't cut on every beat. Most intensities ≤ 0.5 so the few big ones read.

## Licence
Remotion is free for individuals and companies of up to three people; larger companies need a Remotion
company licence (remotion.dev/license). npm installs it; this folder doesn't include it.
