# 07 · Digital Scrapbook

Tokens: `style.json`. Craft for every style: `MOTION_RULES.md`. Reference evidence: ref7 (`styles/_research/REFERENCES.md`
— sheet, bursts at 2.38 s and 8.04 s, seam cut05 silhouette → thermal; ≈8 cuts per 10 s, each scene internally
animated on twos).

**Idea:** an archive of a life. A personal manifesto told with the things someone kept: pixel keepsakes, typed
thoughts, pen marks, a heat-signature of a person. Nostalgic and curious, a little chaotic, handmade on purpose —
every drawn thing moves on twos like it was animated by hand.

## Visual grammar & composition
- **Paper page:** soft grey `#E7E6E8` with a heavy dark vignette that breathes (stronger while a thought is being
  typed). Black scenes for the inner world (thermal, captions in white).
- **Huge cropped type** over dashed guide lines (cap/baseline) that bleed off both edges, the last letters cropped
  off frame and slightly greyed.
- **Keepsakes** are original 8-bit sprites with hard black pixel outlines (coin, heart, cassette, plant, pad,
  floppy, star, camera), arranged in a loose orbiting ring, never a tidy grid.
- Doodles live on top: red ballpoint circles and zig-zags, scribble bursts, a creator handle in a pixel font.

## Typography
- Display: heavy, tight grotesk (Bricolage Grotesque 800, −6 to −7.5 %), lowercase, enormous and cropped.
- Captions: small bold sentence/lowercase (Manrope 800) **typed word by word** with a solid block cursor that
  blinks on 8-frame cycles; a sentence may continue across a cut.
- Pixel font (Silkscreen) only for the handle and tiny UI labels.

## Colour & material
Paper grey, near-black `#0B0B0C`, ballpoint red `#E4312B`, electric cyan `#1FA4FF` (flare only), spark orange
`#FF7A1A`, coin yellow. Thermal palette is a gradient map: black → `#8C0D12` → `#FF5A0F` → `#FFC22E` → pale yellow.
Film grain over everything; nothing glossy.

## Animation curves & timing
- **On twos** (`motion.step: 2`, `stepFrame`): pops, doodles, drifts and typing change every 2nd frame.
- Pops overshoot in discrete steps (0.55 → 1.3 → 0.9 → 1.0 over 6 frames); `ease.enter` overshoots by design.
- Scenes last 15–35 frames; flash frames 2–4 frames; holds are short but never dead (cursor blinks, ring turns).

## Transition vocabulary (the seven techniques, this style's way)
- **Object continuity — pop-in with burst** (signature): each keepsake arrives with an orange spark burst or a black
  ink puff, staggered 3 frames, building the ring.
- **Flare sweep:** a cyan four-point lens flare sweeps in from the frame edge, blooms and whites out into the next
  scene (a light-matte wipe).
- **Flash frame:** a red frame with a white zig-zag scribble burst, two drawings alternating on twos — a hard cut
  with an exclamation mark.
- **Exposure ramp:** a dark silhouette's luminance ramps through the heat gradient map (luminance → thermal).
- **Motion continuity:** typed words continue across scenes; loose letters drift and are circled by pen.
- **Push/pull & perspective:** depth-of-field — ring objects at the back blur, the front ones sharpen; no 3D camera.
- **Match cut / seamless zoom:** a letter or sprite becomes the next scene's shape (heart → red flash; coin → sun).
- Avoid: smooth corporate eases, crossfades, glossy 3D, clean vector icons.

## Camera
A flat scrapbook page. Small drifts, focus pulls, occasional smear frames; the energy is in cuts, pops and doodles.

## Lighting & texture
Flat paper light under a breathing vignette; black scenes lit only by heat colour and the flare. Grain, ink,
pixel edges (`shape-rendering: crispEdges`).

## Sound
Lo-fi tape hiss, a keyboard click per typed word, 8-bit blips on pops, a flash zap on the red frame, a warm pad
under the thermal shot.

## Do / don't
Do: twos, pixel sprites with outlines, typed captions with a block cursor, red pen, vignette, gradient-map heat.
Don't: smooth everything on ones, use stock emoji or vector icons, crossfade, make it tidy or corporate.

## Motion grammar
Hand-made and stepped: everything drawn changes on twos (`stepFrame`), pops overshoot in discrete steps (0.55 →
1.3 → 0.9 → 1.0), typing lands per word on 3–5-frame steps, the cursor blinks on 8-frame cycles, drifts crawl on
twos. Scene changes are fast (≈8 per 10 s) but no scene is static inside: one hero action (type, pop, sweep, heat)
plus one or two ambient wobbles (vignette, cursor, ring). Smear frames instead of motion blur; nothing glides.

## Scene archetypes
- **A · Cropped manifesto word** — two huge lowercase words cropped off the right edge over crawling dashed guides;
  letters pop in pairs on twos.
- **B · Typed thought** — one small bold sentence typed word by word behind a block cursor; the vignette closes in.
- **C · Keepsake ring** — 6–8 pixel sprites pop into a loose orbiting ring with spark bursts / ink puffs, focus
  falling off toward the back; a caption types below.
- **D · Heat portrait** — a silhouette on black ramps into the thermal gradient map; a white typed caption.
- **E · Doodled end card** — loose letters drift and turn, a red ballpoint loop circles one, the handle.

## Sequence model
hook (A, ~12 %) → thought (B, ~22 %) → archive (C, ~24 %) → inner (D, ~20 %) → sign-off (E, or A again to loop,
~22 %). Carried by `typed-carry`, `flare-sweep`, `flash-frame`, `heat-ramp`, `pop-build`, `sprite-match`. Fast hard
cuts on clicks and snares; each scene animated on twos inside.

**Example mini-edit (15 s):** 1 "what do" pops in letter by letter → *typed-carry* → 2 "what do you keep when
everything changes?" types, the vignette tightens → *flare-sweep* → 3 eight keepsakes pop into the ring, "the
small things." → *flash-frame* (3 red frames, white zig-zag) → 4 a profile heats up, "you keep them." →
*typed-carry* → 5 k-e-p-t drift loose, the pen circles the k, @lost.and.kept; hold on the blinking cursor.

## Element behaviour
Display words pop in by letter; captions type by word and may run across a cut; nothing fades in. The handle pops
in with a burst and sits in a corner. Icons are original 8-bit sprites with black outlines, never vectors or emoji.
Images become thermal or grainy and enter by heat ramp or flash cut. Shapes are ballpoint strokes drawn with a
wobble, flare stars, scribble bursts, crawling guides. Background: grey paper under a breathing vignette, black
for inner scenes. Particles: spark bursts and ink puffs on pops, 4–8 frames, on twos.

## Audio relationship
Syncopated lo-fi foley: a keyboard click per typed word, an 8-bit blip per pop on the off-beats, a zap on the red
frame, a tape whoosh peaking at the flare's white, a warm pad under the heat portrait, tape hiss throughout.
