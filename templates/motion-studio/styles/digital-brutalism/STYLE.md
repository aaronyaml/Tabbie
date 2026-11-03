# 02 · Digital Brutalism (screen culture, ASCII, CRT)

Tokens: `style.json`. Craft that applies to every style: `MOTION_RULES.md`. Reference evidence: ref5
(`styles/_research/REFERENCES.md`: sheet, bursts at 0.70 s / 5.83 s / 10.87 s, seam cut03 — rolling counter with
pixel glitch frames, typed captions with block cursor, curved collage plane, dither → dot-matrix, inversion cuts).

**Idea:** the screen is the material. Nothing pretends to be glass or paper: pixels, scanlines, glyphs and
coordinates are shown raw, and the edit stutters like a machine thinking. Black and white do the talking; one hot
flash colour lives only inside images.

## Visual grammar & composition
- Pure black `#000` or paper white `#F2F2EE`, never grey grounds. Whole-frame polarity flips are the scene
  changes.
- Huge, cropped dot-matrix type (a number or one word) *or* one small lowercase mono line, centred, with a block
  cursor. Never medium sizes in between.
- Machine chrome in the corners: coordinate read-outs (`x: 0199 y: 0267`), buffer counters, a progress bracket,
  dashed crosshair lines that track a point. Small, mono, never decorative copy.
- Images live on a **curved, barrel-warped screen plane** with a slight perspective, its edge drawn as a hairline.

## Typography
- Display: Doto (square-dot LED matrix), uppercase or numerals, set huge (400 px+), tight. Big words can also be
  *built* from the image's own cells (a 5×7 bitmap scaled 2×, drawn as round dots).
- Text: Space Mono lowercase, word-by-word typewriter, block cursor that blinks at 8 f; IBM Plex Mono for data.
- Type never eases in: it appears (typed, stepped, or resolved from glyphs).

## Colour & material
- Black, white, mid grey `#6B6B6B` for dim ASCII. One hot flash ramp — `#3A0A05 → #FF4A1C → #FFB25A → #FFF6E8` —
  only inside dithered imagery (flash photography, light leaks), never on type or UI.
- Material vocabulary: scanlines (3 px pitch), ordered Bayer dither, halftone dots, ASCII ramp ` .:-=+*#%@`,
  pixelation, slice offsets.

## Animation curves & timing
- Stepped everything: `steps:N` eases for enter/exit/morph, so motion snaps between states; only the image plane
  drifts with a smooth `camera` curve.
- Rhythm = stutter: 2–3-frame glitch pairs, then a calm typed line, then a burst. Counters accelerate (power ease)
  and land on a held number with a `+`.
- Glitch frames are punctuation (1–2 frames), never a constant effect.

## Transition vocabulary (the seven techniques, this style's way)
- **Polarity inversion** (signature cut): after a 2-frame slice/pixel glitch the whole frame flips white ↔ black.
- **Seamless zoom — resolution cascade:** image → dither → dots → ASCII → letters; each cell switches on its own
  stepped schedule so the change ripples, and the letters are the image's own cells (object continuity).
- **Track matte:** a scan-in (rows appear top-down) or a word mask through the ASCII field reveals the next layer.
- **Match cut:** a block cursor becomes a pixel, a pixel a dot, a dot the next word; a crosshair lands on the next
  read-out.
- **Motion continuity:** horizontal smear frames carry an exit sideways; the next scene enters from the smear.
- **Perspective:** the curved plane tilts 5–10° and eases flat; one vanishing point.
- **Push/pull:** stepped scale jumps (2–4 steps), never a smooth zoom.
- Never: crossfades, soft blurs, springs with bounce.

## Camera
Screen-space. The image plane drifts and tilts slowly; everything else is locked to the screen grid. Smear frames
(directional blur 60–140 px) instead of camera moves.

## Lighting & texture
The screen is the light: emissive white, hot flash highlights burned into dither, a hard CRT vignette, scanlines
over everything, light grain.

## Sound
Keyboard clacks per typed word, CRT whine bed, short digital crunches on glitch frames, a sub thump on every
polarity flip, counter ticks accelerating into the hold. Music: dry, minimal, electronic.

## Do / don't
Do: black/white, huge cropped dot-matrix numbers, typed mono captions, coordinates, curved image plane, the
dither → dots → letters cascade, inversion cuts, stepped timing. Don't: gradients, glass, soft shadows, coloured
type, smooth eases on type, constant glitch, fake hacker green, crossfades.

## Motion grammar
Stepped and mechanical. States snap; nothing tweens except the image plane's slow tilt. The edit stutters: a calm
typed line (1–1.5 s), a 2–3-frame glitch group, a hard polarity flip, a held number (1–2 s), repeat. Counters and
scans accelerate into the stop with no settle. One thing moves at a time; the corner chrome ticks while it holds.

## Scene archetypes
- **A · Typed line** — black, one lowercase mono line typed word by word behind a block cursor, dim ASCII behind,
  read-outs ticking in a corner.
- **B · Counter slam** — paper white, a huge cropped dot-matrix number on rolling reels with glitch frames, slamming
  into the held value with a "+"; a crosshair locks on; a caption types under it.
- **C · Curved image plane** — a barrel-warped plane scans in row by row, a flash photo as hot-ramp dither, the plane
  tilts 5–10° and eases flat.
- **D · Glyph word** — the plane's cells cascade dither → halftone → ASCII → round dot letters; the name holds while
  the ASCII around it flickers.
- **E · Terminal sign-off** — black, the name or URL typed small, the cursor blinking alone.

## Sequence model
opening (A, ~15 %) → development (B, ~25 %) → escalation (C, glitch-pair cuts between images, ~20 %) → reveal
(D, ~25 %) → resolution (E, ~15 %). Every change is a flip, a scan or a cascade (presets in `style.json`
`transitionPresets`: polarity-flip, resolution-cascade, scan-in, glitch-pair, cursor-match, smear-exit).

**Example mini-edit (12 s):** 1 "noise is a material." types on black → *polarity-flip* → 2 white: 48210 rolls on
dot-matrix reels and slams, "+", "nodes online right now" types → *polarity-flip* → 3 the curved plane scans in, a
dithered flash photo, slow tilt → *resolution-cascade* → 4 the photo becomes dots, ASCII, then "STATIC" in dot
letters → *smear-exit* → 5 black, "static.dev" types, the cursor blinks alone.

## Element behaviour
Text is typed or appears in one step; it never eases or fades. The logo is built from the image's own cells or
typed, and the cursor signs it off. Icons are pixel glyphs. Images only on the curved plane, dithered, and they
leave as dots and glyphs. UI is raw screen (mono read-outs, brackets, counters), no cards. Shapes are cells, dots,
crosshairs. The background is pure black or paper white, flipped whole; scanlines on everything. No particles
beyond cells, glyphs and glitch slices.

## Audio relationship
Syncopated and keystroke-driven: typed words on keyboard clacks, glitch pairs on off-beat hits, polarity flips on
downbeats with a sub thump, counter ticks accelerating into the slam, a tape-stop crunch on smears, CRT whine as
the bed.
