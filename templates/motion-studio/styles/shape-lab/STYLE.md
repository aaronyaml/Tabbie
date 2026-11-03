# 10 · Shape Lab

Tokens: `style.json`. Craft that applies to every style: `MOTION_RULES.md`. Reference evidence: ref9 in
`styles/_research/REFERENCES.md` (a 30 s design reel, 40 shots). Principles only; no frame is copied.

**Idea:** a design tool on fast-forward. Pure primitives (rectangles, circles, rings, bars, star bursts) on an
electric-blue blueprint grid snap, slide and morph into each other. While they transform, their edges render
pixel-stepped and they can dissolve into dither. Hard cuts on the beat keep the composition and flip the palette.
Graphic, confident and playful; a moment of 3D or line moiré now and then shows the craft.

## Visual grammar & composition
- One to three primitives on a clear axis (centred, or a pair left/right), lots of flat colour around them.
- A fine blueprint grid (thin lines at ~10 % of the ink) sits behind everything and never moves.
- Hairline guides, bounding boxes and tiny handle marks appear briefly during a move, like a selection in a
  design tool, then vanish.
- Full-frame splits are welcome: a white column in a blue field, two black panels with tiny UI marks.

## Typography
- Almost none. Inter Tight Light (300) for one word or a number, often as an outline or a single thin red line;
  Inter Tight 600 for a name. JetBrains Mono (lower case) for a tiny caption or credit.

## Colour & material
- Electric blue `#0B4AFA`, paper white `#FAFAFB`, black `#030306`, gold `#F5B214`, a red line `#E8213B`, deep
  blue `#0A2A8F` for 3D sides and thick rings.
- One palette per shot. A palette-flip cut swaps the roles (blue/white → black/gold → white/blue) while the layout
  stays.
- Flat vector fills, no gradients or shadows, except on the rare 3D beat (a satin slab under soft light).

## Animation curves & timing
- On ones, very snappy. Morphs and slides use a hard in-out (`morph` [0.76,0,0.24,1], `move` [0.83,0,0.17,1]):
  still → fast → dead stop. Pops use a short overshoot spring.
- While a shape is "rendering" (mid-morph, dissolving, rings turning) its edges are pixel-stepped: quantise
  geometry to 8–16 px blocks and step time (`pixel`: steps 6–10).
- Holds are short (4–10 f); the montage averages ~0.75 s per shot.

## Transition vocabulary (the seven techniques, this style's way)
- **Object morph — shape morph** (signature): rect → pill → circle, two blocks → one bar, a circle → rings.
- **Rhythmic cut — palette-flip cut** (signature): same composition, swapped palette, on the beat; 2–4 in a row
  accelerate a phrase.
- **Destruction/rebuild — pixel dissolve** (signature): the edge steps, the form breaks into ordered-dither
  particles and they reform as the next shape.
- **Seamless zoom — ring zoom:** the circle grows past the frame while rings spawn inside; the inner ring is the
  next circle.
- **Wipe — burst wipe:** a star burst pops at an edge and covers the frame; the next shot is inside it.
- **Perspective — slab tip:** a flat rect extrudes and tips over on a white floor, then cuts flat.
- **Match cut:** the same primitive at the same place across a cut (circle → planet of dither → ring).
- Never a soft crossfade.

## Camera
Locked and frontal on the grid. Punch-ins are cuts. Only the depth beats move: a slow orbit round a wireframe, a
fixed perspective for the tipping slab, a moiré ribbon sweeping through depth.

## Lighting & texture
Flat. Grain is barely there (≈ 2 %). Pixel steps and dither are the texture, and only while something transforms.

## Sound
Tight electronic: clicks and blips on cuts and snaps, short whooshes on morphs, bit-crushed crackle on dissolves,
pops with sparkle on bursts, a synth pulse under the montage.

## Do / don't
- Do: primitives only, one palette per shot, snaps on beats, pixel steps mid-move, return to the opening layout.
- Don't: photos, gradients on flat shapes, drop shadows, soft eases on everything, crossfades, many words.

## Motion grammar
- **Pacing:** fast montage (~13 cuts per 10 s) with one or two 2–3 s morph breathers.
- **Rhythm:** beat-cut; morphs fill the bar and land on the downbeat.
- **Easing:** hard in-out, snappy overshoot pops, stepped while pixel-rendered; never linear drift.
- **Movement:** primitive morphs, snap slides, scale pops, star bursts, dither dissolves, rotating rings, a
  tipping slab, sweeping moiré.
- **Density:** 1–3 shapes most of the time; two or three dense bursts (particles, moiré, wireframe).

## Scene archetypes
- **A · Primitive pair:** one to three white primitives on the blue grid; snap, slide, merge.
- **B · Ring orbit:** a circle with concentric rings and broken arcs turning at different speeds.
- **C · Burst field:** star bursts and sparkles breaking out of a blob, dither debris, palette flips.
- **D · Depth beat:** an extruded slab, a wireframe rig or a moiré ribbon; the flat world gains depth.
- **E · Number plate:** a hairline frame with corner sparkles and a thin traced number or word.

## Sequence model
Opening (15 %, A) → shape morph → development (25 %, B/A) → ring zoom → escalation (25 %, C/D, accelerating
palette flips) → pixel dissolve → reveal (20 %, E) → palette flip → resolution (15 %, A + E). 8–30 s.

**Example mini-edit (12 s design-tool launch):** 1 · two white blocks snap together into a bar (0–54) → morph →
2 · the bar rounds into a circle, rings spawn and turn pixel-stepped (54–126) → ring zoom → 3 · a star burst pops
from a blob, three palette flips on the hi-hats, debris dithers away (126–198) → dissolve → 4 · particles reform
as a slab that tips over (198–246) → flip → 5 · a hairline plate with corner sparkles, "v2" traced in red
(246–318) → flip → 6 · the two blocks again, the name small between them (318–360).

## Element behaviour
- **Text:** rare, small, thin; traced or outlined. **Logo:** reduced to its primitives, which morph into the
  mark. **Icons:** primitives or star bursts. **Images:** avoided, or rasterised into dither. **UI:** selection
  boxes, guides and handle marks only. **Shapes:** the whole language. **Background:** blue grid, flipped on cuts.
  **Particles:** dither specks and pixel blocks, never glowing sparks.

## Audio relationship
Beat-driven and syncopated. Every cut and snap on a beat; palette-flip runs on hi-hat or snare rolls; morphs land
on the downbeat; the densest beat (moiré, wireframe) on the drop. SFX: clicks, blips, whooshes, crackle, pops,
one thud.
