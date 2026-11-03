# 03 · Retro Editorial (heritage packaging, tiled wall)

Tokens: `style.json`. Craft that applies to every style: `MOTION_RULES.md`. Reference evidence: ref3
(`styles/_research/REFERENCES.md`: sheet, bursts at 2.17 s / 8.13 s / 10.77 s — teal tiled wall with dark grout,
tile-by-tile scattered re-tiling into red/sand blocks, stamps fanning, slow 3/4 drift, 2 cuts in 16 s).

**Idea:** a warm, crafted world on a permanent set. The tiled wall never cuts away; it *re-tiles*. Objects are
printed things (stamps, labels, tins, posters) shown with care, drifting slowly in soft daylight. Heritage without
pastiche: real material, restrained type, unhurried time.

## Visual grammar & composition
- The set is a grid of glazed tiles (≈120 px at 1080p, 16×9) with thin dark grout. It is always visible, at least
  at the edges. Tiles carry slight tone variation and a soft inner sheen.
- Scenes are **colour-block compositions on that grid**: teal field; red field with a sand column; a sand block
  behind a product. Blocks always snap to whole tiles.
- One hero object (stamp, tin, label) on a third, in 3/4 or slight rotation, with a soft contact shadow; a
  wordmark on the wall opposite it. Small spaced caps folios in the corners.

## Typography
- Wordmark: a soft, rounded serif (Fraunces with SOFT/WONK, or DM Serif Display), lowercase, large, cream on
  colour; slight printed offset shadow.
- Support: DM Sans bold, **uppercase with 0.3 em+ tracking** ("casa de conservas · est. 1931"), small.
- Stamp numerals in the serif, red or navy. Type rises out of a baseline mask, letter by letter, slowly.

## Colour & material
- Teal `#3E93A9` (wall), brick red `#B5382A`, sand `#E8C98E`, cream paper `#F1E6CF`, navy ink `#1E3A6B`, grout
  `#13191C`. Never neon, never gradients as fills.
- Materials: glazed ceramic, uncoated paper with fibre, perforated stamp edges, engraved/etched line illustration
  (navy on cream, multiplied into the paper), guilloche rosettes, tin and printed labels.

## Animation curves & timing
- Slow and warm: standard 16 f, slow 30 f, drifts over the whole shot. Ease-out enters, gentle ease-in exits, no
  overshoot to speak of (`springs.paper/stamp` are heavy and damped).
- Re-tiling: each tile switches on its own frame (quantised to 2 f), clustered in 2×2 groups, spreading from an
  origin with scatter; about 22–26 f per wipe. Some tiles pass through sand for 4 f on the way.
- On ones, no motion blur. Drift is periodic so loops are seamless.

## Transition vocabulary (the seven techniques, this style's way)
- **Track matte — mosaic re-tile** (signature): the wall *is* the matte; the next scene's colours (or imagery)
  appear tile by tile in scattered clusters. Never a cut or a fade on the wall.
- **Object continuity — the deck:** stamps slide out from under the hero like a fanned deck and gather back; a tin
  lid lifts into a flat lay.
- **Match cut:** an oval stamp frame → a tin label oval; a tile → a stamp; a printed pattern ribbon → the wall.
- **Push/pull:** a slow push of a few percent over a whole scene, never a whip.
- **Motion continuity:** objects keep drifting through the re-tiling; the wall changes, the object doesn't stop.
- **Perspective:** 3/4 product views and top-down flat lays; the wall stays frontal.
- **Seamless zoom:** into a stamp's engraving until its texture becomes the next tile colour (use sparingly).
- Avoid: glitch, whip pans, motion blur, crossfades, hard cuts on the set.

## Camera
Locked frontal on the wall; objects drift a few px and ±2°. Occasional top-down flat lay. Never handheld.

## Lighting & texture
Soft warm daylight from the upper left, long gentle contact shadows, a faint glaze sheen, warm vignette, paper
fibre on printed things, light grain.

## Sound
Clustered soft tile clicks as the wall re-tiles, paper slides and stamp thuds, a light acoustic/bossa bed, room
tone. Nothing synthetic.

## Do / don't
Do: the tiled wall as the set, mosaic re-tiles, cream/red/sand/navy, stamps and engraving, soft serif + spaced
caps, slow drift, soft shadows. Don't: neon, gradients, glitch, fast cuts, sans-only type, glossy 3D, crossfades,
copying real packaging.

## Motion grammar
Unhurried and continuous. Objects drift on ones (a few px, ±2°, soft eases, heavy damped springs, no overshoot);
the only quick events are single tiles flipping, and they come in scattered clusters about 2 f apart, so even the
wall's change reads as a slow ripple. One change at a time; after each re-tile the composition holds 1.5–3 s while
the drift continues. Scenes run 3–6 s.

## Scene archetypes
- **A · Hero on the wall** — teal wall, one stamp or tin on a third in 3/4 with a soft contact shadow, drifting.
- **B · Colour-block field** — the wall re-tiled into a layout snapped to whole tiles (red field, sand column); the
  hero drifts through.
- **C · Stamp deck** — three stamps fanned from one point, each a variant; they fan out 4 f apart and gather back.
- **D · Wordmark on the wall** — the soft-serif lowercase name rises letter by letter in cream, spaced caps track
  in above and below, the hero opposite.
- **E · Flat lay** — top-down on a cream or sand field: a tin lid lifts, a label turns.

## Sequence model
opening (A, ~20 %) → development (B + C, ~25 %) → detail (E, ~20 %) → reveal (D, ~20 %) → resolution (A again,
~15 %). The wall re-tiles instead of cutting; the only straight cut is a printed match cut (presets in
`style.json` `transitionPresets`: mosaic-retile, deck-fan, drift-through, shape-match, engraving-zoom).

**Example mini-edit (20 s):** 1 a cream stamp with an engraved lemon drifts on teal → *mosaic-retile* from the
top-right → 2 red field with a sand column; two more stamps fan out from under it → *shape-match* (the stamp's oval
→ a tin's oval label) → 3 top-down flat lay, the lid slides off → *mosaic-retile* → 4 "limona" rises in cream,
"since 1920" tracks in → *mosaic-retile* back to teal from the bottom-left → 5 the deck gathers, one stamp drifts.

## Element behaviour
Text rises out of a baseline mask, slowly; caps track in; nothing is typed or glitched. The logo is printed (stamp,
label, or large on the wall) and leaves by sinking or under a re-tile. Icons are engraved vignettes and stamp
numerals. Images are printed things with paper fibre and soft shadows. No screens or UI. Shapes: tiles,
perforations, double-rule frames, ovals, rosettes. The background is always the wall and changes only by
re-tiling. No particles.

## Audio relationship
Ambient and foley-led: re-tiles start on phrase starts with their clusters of soft ceramic clicks following the
strum, stamps land on soft downbeats, holds sit on sustained chords; paper slides, a tin lid, room tone over an
acoustic or bossa bed.
