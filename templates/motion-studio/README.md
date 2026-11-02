# Motion studio (from Tabbie's template)

Tabbie's Motion panel and `/motion` jobs make films here. Tabbie created this folder the first time Motion
opened, and ran `npm install` in it (Node comes from `bin/install-tool node`).

- `CLAUDE.md`: how the studio is laid out and the commands jobs use. `MOTION_RULES.md`: the house style.
- `public/audio/sfx/`: 110 ready-made sound effects (catalogue in `manifest.json`, `<Sfx>` in `src/audio`).
  They work without ElevenLabs.
- Optional, for "Generate audio" and new sound effects: add your ElevenLabs API key (the Motion panel's
  **Connect ElevenLabs** button, or copy `.env.example` to `.env`).
- Try it: `npm run studio` (preview) or `npm run render -- welcome output/welcome.mp4`.

Remotion's licence: free for individuals and companies of up to three people; see remotion.dev/license.
