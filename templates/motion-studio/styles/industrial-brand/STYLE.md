# 05 · Industrial Brand

Tokens: `style.json`. Craft for every style: `MOTION_RULES.md`. Reference evidence: ref2 (`styles/_research/REFERENCES.md`
— sheet, 24 shot starts, seam cut10; a 35-shot montage cutting every 4–5 frames, then holds).

**Idea:** an identity is proven by how many hard, real surfaces it survives. One mark, stamped on steel, webbing,
cladding, labels and drawings, cut on a mechanical pulse. Nothing inside a shot needs to move: the edit is the
animation and the mark is the continuity.

## Visual grammar & composition
- **The mark owns a zone.** Every application puts the mark in the same area of frame at a similar size (ref2: the
  symbol sits near frame centre across truck, strap, tote, container). The world changes around a fixed eye point.
- **Applications, not decorations:** container side, webbing strap running diagonally off-frame, building cladding
  shot from below, cargo label, flag, workwear, a logo construction drawing, a pure colour field. Each is a still,
  shot square-on or from below, extremely cropped.
- **Colour blocking:** orange field with an inset black panel, thirds split into orange / black / concrete; block
  edges sit on one grid so they align across cuts.
- **Frame furniture** recurs on every shot: rotated monospace captions on the edges, an index ("APP. 07 / 20"),
  so the montage reads as one system sheet.

## Typography
- Wordmark: geometric/grotesk sans, lowercase, tight (−4 %), heavy (Archivo 700–800).
- Captions: monospace uppercase, small, +8 % tracking, often rotated 90° along an edge ("HEAVY CARGO & TERMINAL
  SERVICES").
- **One huge cropped letterform** bleeding off two edges (ref2's giant "A"), used as a shot of its own.
- Construction drawings: hairline 1 px strokes, tiny dimension labels ("100 U", "R 18", "22.6°") in the accent.

## Colour & material
Vermilion `#FF5233`, near-black `#121214`, concrete `#E9E4E2` / `#D6D0CC`, graphite `#3A3A3C`. Orange is a
material, not a highlight: full container sides, straps, fields. Real matter — corrugated steel, stitched webbing,
brick, overcast sky — with hard raking light and matte shadows. No gloss, no glow, no gradients-as-decoration.

## Animation curves & timing
- **Cuts on a 16th-note pulse:** shots alternate 5 / 4 frames (≈115 BPM) for 2–3 s, then a long hold (1.5–2 s).
- Inside a hold, only drafting moves: hairlines draw on (`ease.move`), labels type on, a colour block slides in
  edge-aligned (`ease.enter`, 6–8 frames). A 1–2 % camera creep at most.
- No overshoot, no bounce (`springs.stamp` is near critically damped — it lands like a press). Motion blur off.

## Transition vocabulary (the seven techniques, this style's way)
- **Match cut on the mark** (signature): the mark holds position and scale while the surface under it changes;
  the hard cut is invisible to the eye on the mark, loud everywhere else.
- **Object continuity:** the mark is the object — on the container, then the strap, then the label.
- **Motion continuity:** a diagonal (strap at −32°) answered by the opposite diagonal (+24°) two cuts later.
- **Track matte:** a colour block (orange column, black base) wipes in on the drawing's grid lines and frames the
  wordmark.
- **Push/pull:** a tight re-crop of the same application (facade → facade tight) is the push; no camera move.
- **Perspective:** architecture from below, the strap in false depth; never a 3D camera.
- **Seamless zoom:** zoom into the construction circle to land on the mark's real application.
- Avoid: crossfades, floaty camera, glow, cutting off the pulse.

## Camera
Locked off. The montage is the camera. Holds may creep 1–2 % toward the mark.

## Lighting & texture
Hard daylight, overcast sky, matte print; fine static grain (`texture.grain`). Photography desaturated slightly so
orange stays the loudest thing in frame.

## Sound
Dry 110–120 BPM pulse, a mechanical hit per cut (ratchet tick, latch, shutter, clack); the hold drops to a low
drone with a single heavy thud as the mark lands.

## Do / don't
Do: mark in one zone, real materials, edge-rotated mono captions, extreme crops, cut exactly on the pulse, then
hold. Don't: animate inside montage shots, move the mark between zones, add glow or gloss, crossfade, use more
than one accent colour.

## Motion grammar
Two speeds and nothing between: the montage at the pulse (a new still every 5/4/5/4 frames ≈ 115 BPM for 2–3 s)
and the dead-still hold (1.5–2 s). Cuts are `steps(1)`, never eased. Inside a montage shot nothing moves; inside a
hold one drafting process moves at a time — hairlines like a plotter (`ease.move`, hard start, dead stop), a colour
block in 6–8 frames (`ease.enter`, no overshoot), mono captions typed character by character. The camera only
creeps 1–2 % across a hold. Light/dark tones alternate so every cut reads.

## Scene archetypes
- **A · Application still** — one extreme crop of a real surface (container, strap, cladding, label), the mark in
  its zone, edge caption + index. 4–5 frames, zero motion.
- **B · Colour-block field** — orange field with a black panel, or thirds orange / black / concrete. Opens a film
  (holds a beat) or punctuates the montage.
- **C · Construction hold** — concrete ground, the mark solid; grid lines, circles and dimension ticks draw on
  around it; labels land on beats.
- **D · Cropped letterform** — one letter of the wordmark at poster size, bleeding off two edges.
- **E · System lockup** — the drawing left, an orange column with a black base wipes in on the right grid line; the
  wordmark sits on the base, the descriptor types on; hold.

## Sequence model
opening (B, ~8 %) → montage (A/D/B, ~37 %) → hold (C, ~22 %) → burst (A/D re-crops, ~13 %) → lockup (E, ~20 %).
Carried by `pulse-montage`, `land-on-hold`, `construction-zoom`, `block-wipe`; every montage cut is a
`mark-match-cut`, a tighter re-crop is the `recrop-push`. Cuts only on the pulse; the montage always lands on a hold.

**Example mini-edit (15 s):** 1 orange field, black panel, the mark lands with a thud → *pulse-montage* → 2 thirty
stills at 5/4 frames, the mark never leaves its zone, index APP. 01 → 20 → *land-on-hold* → 3 everything stops; the
construction drawing draws itself, 100 U / R 18 land on beats → *construction-zoom* through the circle → 4 a shorter
burst of tight re-crops → *block-wipe* → 5 the orange column slides in, "halden" on the black base, the descriptor
types; hold 2 s.

## Element behaviour
The wordmark is stamped, never animated in. Mono captions type on only inside holds; edge captions sit rotated on
every shot. The mark is the continuity: same zone, near-same size, never moves inside a shot. Images are stills of
real applications, desaturated so orange stays loudest. No UI, no icons beyond construction diagrams. Shapes are
grid-aligned colour blocks and hairlines that draw. Backgrounds are flat fields or the material. No particles.

## Audio relationship
Beat-driven and mechanical: every cut on a 16th note of a dry 110–120 BPM pulse, one hit per cut (ratchet, latch,
shutter); the hold starts on a downbeat where the pulse drops out to a low drone, and the mark's landing gets one
heavy thud.
