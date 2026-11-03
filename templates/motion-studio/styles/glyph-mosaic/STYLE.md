# 09 · Glyph Mosaic

Tokens: `style.json`. Craft that applies to every style: `MOTION_RULES.md`. Reference evidence: ref8 in
`styles/_research/REFERENCES.md` (one continuous 16 s piece). Principles only; no frame is copied.

**Idea:** everything is made of tiny printed glyphs. A mark, a person, a word: each is a mosaic of geometric glyph
tiles in process colours on warm paper. Motion is the mosaic re-organising itself: re-resolving between coarse and
fine, re-patterning in waves, spiralling into a vortex, and carrying the eye through a single glyph into the next
form. It feels generative and alive but hand-printed, never neon.

## Visual grammar & composition
- One centred form on a lot of empty paper (30–45 % of the frame height), or one full-bleed field of concentric
  glyph rings around a negative-space silhouette. Nothing else on screen.
- Glyphs sit on a grid (pitch 14–40 px at 1080p). Density and glyph size carry tone: small dense glyphs for
  shadows and the core, big sparse glyphs at the edge.
- Colour clusters by region like a halftone separation: cyan for the base, magenta for the face, yellow for an
  edge light, black/grey for the shadow side. Never a random confetti mix.

## Typography
- Few words. Space Grotesk (500/700) for a name or one word; Space Mono (lower case) for captions.
- Letters can be spelled in glyph pixels (a 5 × 7 glyph matrix per letter) and assemble on a wave.

## Colour & material
- Paper `#F3F0E6`, ink `#151515`, magenta `#E2187F`, cyan `#21B4D6`, yellow `#F2DE1B`, green `#26B86A`.
- Flat process colours with `mix-blend-mode: multiply` and a 1–2 px misregistration on rings, so overlaps mix like
  ink. Fine paper grain (≈ 6 %). No gradients, shadows or glows.

## Animation curves & timing
- On ones. Smooth: `morph` [0.45,0,0.55,1] for regrids, `camera` [0.7,0,0.3,1] for blooms and zooms, a soft spring
  for single glyphs popping or flipping.
- Every change is a wave: a per-glyph stagger of 0.5–2 f by distance from a point (centre or an edge).
- Continuous: a transformation every 1–3 s, holds of 10–20 f where the form reads, glyphs always shimmering.

## Transition vocabulary (the seven techniques, this style's way)
- **Object morph — re-resolve** (signature): sample the next shape on the same grid; shared glyphs ease to their
  new cells, others shrink out or grow in. Coarse → fine reads as "resolving".
- **Seamless zoom — glyph zoom:** push into one glyph until it fills the frame; its parts become the next scene.
- **Seamless zoom — vortex bloom:** the form re-resolves into the vortex: its glyphs fly out to become the inner rings
  while the outer rings bloom to full bleed and the field turns; reversed, the rings fold back into the form.
- **Motion continuity — split and scatter:** a hero glyph splits into parts that travel on the grid and become groups.
- **Rhythmic change — glyph wave:** tiles flip glyph or colour in a wave without anything moving.
- **Match cut:** the same silhouette as glyph portrait and as negative space inside the vortex field.
- **Track matte:** the negative-space silhouette is itself a matte for the field.
- Never a hard cut or a crossfade: the mosaic is always continuous.

## Camera
Locked off and flat; the field breathes ±4 % in scale. The only moves are pushes into a glyph and the bloom's
pull; no rotation of the camera (the field rotates, the camera doesn't).

## Lighting & texture
No light. Paper grain and multiplied ink are the only texture.

## Sound
A continuous, textural bed (soft pulsing synth, granular ticks, chimes) that swells rather than hits.

## Do / don't
- Do: one form at a time, regional colour, waves, a lot of paper, loops that return to the opening form.
- Don't: photos, dense copy, hard cuts, random rainbow confetti, neon glow, motion blur, drop shadows.

## Motion grammar
- **Pacing:** continuous, a transformation every 1–3 s with short readable holds.
- **Rhythm:** wave-driven; the field shimmers as changes ripple across it.
- **Easing:** smooth in-out for regrids and the camera, a soft spring for single glyphs; never linear.
- **Movement:** re-resolve, glyph flip waves, radial vortex, seamless glyph push, breathing scale, colour waves.
- **Density:** high inside forms, empty outside.

## Scene archetypes
- **A · Glyph mark:** the mark or monogram built from glyphs on paper; pops in centre-out, shimmers.
- **B · Glyph portrait:** a person, product or object as a mosaic; regrids coarse to fine.
- **C · Vortex field:** full-bleed concentric rings around a negative-space silhouette; rotates, ripples.
- **D · Glyph hero:** one big glyph (or a 2 × 2 set); rotates, its ring cycles colour.
- **E · Glyph caption:** a word assembled from glyphs with a mono line; the call to action.

## Sequence model
Opening (18 %, A) → re-resolve → development (22 %, B) → vortex bloom → escalation (25 %, C) → glyph zoom →
reveal (15 %, D) → split and scatter → resolution (20 %, E then A). One continuous shot, no cuts; 10–30 s.

**Example mini-edit (15 s AI-product launch):** 1 · the monogram builds centre-out, "made of everything" types
below (0–75) → re-resolve → 2 · it regrids into a coarse bust that resolves fine (75–165) → vortex bloom → 3 · the
bust's glyphs spiral out into the inner rings, the field blooms to full bleed and turns 40° (165–285) → glyph zoom → 4 · a
pixel-cross glyph fills the frame, its ring cycles colour (285–360) → split → 5 · four groups gather into the
product name beside the monogram (360–450).

## Element behaviour
- **Text:** short; assembles from glyphs, dissolves back into them. **Logo:** sampled onto the glyph grid; may
  resolve to its true vector for one beat at the end. **Icons:** become hero glyphs. **Images:** never photos;
  sampled into glyph portraits. **UI:** a device silhouette in glyphs at most. **Shapes:** the glyph kit only.
  **Background:** empty paper. **Particles:** the glyphs themselves.

## Audio relationship
Ambient, textural, swell-driven. Regrids and blooms start on a phrase and ride a swell; glyph waves follow
granular runs or hi-hats; the glyph zoom lands on a bell or chord change. SFX: shimmer, ticks, swells, one bell.
