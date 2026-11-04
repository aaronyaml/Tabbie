# 10 · Blueprint Shapes

Tokens: `style.json`. Craft that applies to every style: `MOTION_RULES.md`. Reference evidence: the blue reel in
`styles/_research/REFERENCES.md` and its frame-accurate replica (`src/compositions/replicas/blue-reel/ANALYSIS.md`,
42 shots). Principles only; no frame is copied.

**Idea:** a graphic beat machine. Flat white geometry on an electric-blue blueprint grid, one small shape event per
shot, and the edit cuts hard on every hit of the music. Shapes slam, pop, fly out and squeeze; the frame flips
palette on the big hits; one-frame glitches mark the cuts. It reads as precise and playful at once: a technical
drawing that has learned to dance.

## Visual grammar & composition
- One idea per shot: two rects, one disc, one ring, one frame, one card. Lots of field around it.
- Ground: electric blue with a hairline white grid (22 or 44 px at 720p, ~13–20 % opacity). Inverted shots use a
  white field with a lavender grid; single shots use navy (iris), void black (flips) or pale grey (dither).
- Symmetry and the centre line: shapes start mirrored about the centre and meet on it; frames and cards are centred.
- Guides are part of the picture: hairline boxes around a shape, dotted arcs linking elements, ghosted panel tiles
  repeating a frame across the field.

## Typography
- Almost no text. When there is, it is one big thin numeral or a short word in Big Shoulders 100–200 (tall,
  condensed, stadium-shaped zero), set huge on a white card, in the red accent or white.
- DM Mono, lower case, tiny, only as UI marks next to a hairline box. Never paragraphs, never a tag or handle.

## Colour & material
- Electric blue `#0A4BFE` (`bg`) and white (`ink` / `surface`) carry 80 % of the film. Navy `#0E1838` (`night`) for
  the iris, void `#030306` and gold `#F5B000` (`accent`) for colour flips, red `#E8143C` (`accent2`) only for
  numerals, pale grey `#D2D2D7` (`muted`) and deep navy `#1C2B8C` (`deep`) for dither fields, lavender `#DADCF0`
  (`grid`) for the grid on white.
- Flat fills, no gradients on shapes, no shadows. Glow only as a soft halo around a sparkle core or the dither disc.
- Textures are digital and hard-edged: 4×4 Bayer dither, dotted string-art fans, glitch blocks.

## Animation curves & timing
- On ones. Inside a shot: expo out `[0.16,1,0.3,1]` for anything arriving (pops, flights, iris breathing), expo in
  `[0.7,0,0.84,0]` for anything leaving or slamming into the cut, quart out for slow drifts, and a springy pop
  (`springs.pop`, ~8 % overshoot plus a small counter-rotation) for stars and bursts.
- A shot lasts 12–30 f (0.4–1 s) with one or two moves and a 4–10 f hold. Pops take 2–4 f; slams accelerate for
  10–12 f and land on the hit.
- Strokes thin as things settle: a ring or frame starts heavy (16–24 px) and eases out to a hairline.

## Transition vocabulary (the seven techniques, this style's way)
- **Rhythmic cut — beat cut** (signature): hard cut to a new composition on every transient; change field colour or
  grid pitch so the cut reads.
- **Flash — glitch hit:** one frame of RGB split (4–14 px), scan streaks and block re-quantise on the hit.
- **Match cut — shape match:** the last shape of a shot is the first of the next in a new role (bar → iris tick,
  dot → ring centre, white band → bar → the opening rects).
- **Flash — colour flip:** the same layout re-inked in void + gold on a downbeat, a burst rises from the edge and the
  camera pulls back ≈8 %.
- **Seamless zoom — iris open / ring zoom:** a disc opens past the frame edge; broken rings scale ×5 past camera
  (expo in) and the cut lands as they leave.
- **Track matte:** the iris disc and the white card are mattes for the next field (grid inside the disc only).
- **Motion continuity:** sparkles keep flying and turning across the cut from frame to card.
- Never a crossfade, never a slow dissolve; overlap is 0–2 f at most.

## Camera
Locked and flat. Shapes move, the camera doesn't; the exceptions are the ring zoom past the lens, the small
pull-back on a colour flip and the iris opening.

## Lighting & texture
No lighting. Clean vector flats; texture comes from dither clouds, dotted fans, hairline grids and glitch frames.

## Sound
A driving electronic track (120–135 BPM). Every cut sits on a transient; glitch frames on snares and crackles; star
pops on 16ths with tiny clicks; colour flips on downbeats with a sub hit; risers peak on the iris open and the ring
zoom.

## Do / don't
- Do: one shape idea per shot, hard cuts on hits, expo curves, stroke thinning, mirrored starts, a palette flip at the
  peak, loops that come back to the opening shape.
- Don't: crossfades, soft gradients, drop shadows, 3D bevels on the flat kit, long holds, paragraphs of copy,
  watermarks, handles or corner tags.

## Motion grammar
- **Pacing:** fast montage, 0.4–1 s per shot; a 1–3 f strobe section at most once.
- **Rhythm:** beat-cut; one or two snappy moves per shot.
- **Easing:** expo out to arrive, expo in to leave or slam, spring overshoot for pops; never linear on shapes.
- **Movement:** slam, pop, fly-out, squeeze-out, iris, condense, stroke thinning.
- **Density:** low inside a shot, high across the edit.

## Scene archetypes
- **A · Shape slam:** two mirrored shapes accelerate together and merge into a bar on the hit (or split from it).
- **B · Frame and sparkles:** an outline frame grows on ghosted panel tiles while two sparkles fly out; a white card
  with a big thin numeral cuts in.
- **C · Primitive play:** a disc squeezes out a dot, a ring thins to a hairline, a square pops off a bar.
- **D · Star bloom:** white field, a blue blob pushes in, a diamond erodes into dither, stars pop one per frame; the
  colour flip to black and gold.
- **E · Texture field:** dither particles condense into a disc and spiral; broken rings strobe and zoom.

## Sequence model
Opening (20 %, A) → iris open → development (25 %, B and C) → beat cut → escalation (20 %, D) → colour flip →
reveal (15 %, E) → ring zoom → resolution (20 %, B then A: the card holds, the white band narrows into the bar).
Hard cuts on the music's hits throughout; 6–30 s.

**Example mini-edit (10 s product-launch teaser):** 1 · rects slam into a bar, glitch, the bar is the iris tick and
the iris opens (0–45) → 2 · a frame grows, sparkles fly out, the card with the launch date cuts in (45–105) →
3 · a disc squeezes out a dot, match cut to thinning rings (105–150) → 4 · white field, blob, stars pop, glitch, flip
to black and gold (150–210) → 5 · dither condenses and spirals, rings zoom past camera (210–250) → 6 · the white
band narrows into the bar, the product's mark (250–300). No tag or handle.

## Element behaviour
- **Text:** one big thin numeral or word on a card. **Logo:** built from the kit (bar, ring + dot, star), arrives as a
  shape match. **Icons:** flat primitives. **Images:** not used; silhouettes or dither. **UI:** hairline boxes and
  dotted guides. **Shapes:** the whole language. **Background:** blue grid, white grid, navy, void, pale grey.
  **Particles:** hard dither dots only.

## Audio relationship
Beat- and impact-driven. Cuts within ±2 f of a transient, glitch frames on snares, pops on 16ths, flips on downbeats,
risers into the iris and the ring zoom. SFX stay tiny (clicks, crackles, one sub hit); the track carries the rest.
