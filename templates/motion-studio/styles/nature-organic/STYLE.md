# 04 · Nature Organic

Tokens: `style.json`. Craft for every style: `MOTION_RULES.md`. Reference evidence: ref6 (`styles/_research/REFERENCES.md`
— sheet, 24 shot starts of 41, seam cut05 sunburst poster → enamel pin; opens on a moodboard grid, then a 4–5-frame
montage of collateral on nature photography).

**Idea:** growth you can see. Real land — moss, bark, hills, leaf macro — and a brand that radiates out of it. The
motion idea is *radiance*: lime bars grow outward from the mark the way light and roots do; everything else is
calm, rooted and warm.

## Visual grammar & composition
- **Open on a moodboard:** a loose cluster of photo tiles, colour chips, a type tile and numerals around the mark,
  generous mint space between them. It is the brand's world in one look.
- **One photograph becomes the frame:** the camera zooms into a tile until it is full-bleed; the mark sits on it in
  the same place it sat in the tile.
- **The sunburst** (radiating lime bars, alternating lengths, butt ends) is the identity's graphic device: around
  the mark on posters, from a corner on a lime field, as a tiny turning sun at the moodboard's heart.
- Collateral as stills: embossed cards on bark, type specimens, numerals in chips, banners, book spines.

## Typography
- Display: a contemporary serif (Fraunces 400–500), sentence case, −3 to −4.5 %; big headlines cropped off an edge
  ("of Growth").
- Companion grotesk (Hanken Grotesk 400–600) for sentences and specimen lines; small mono caps for labels.
- **Numerals live in lime chips** ("96%", "$4.2B", "12k ha"), serif, rounded 14–36 px corners.

## Colour & material
Forest `#0E3B2C`, lime `#8BE04E`, mint `#E9F7EC`, secondary green `#1F6B47`, wood `#A47A4F`. Lime is light — bars,
chips, the mark on dark; forest is the ground; mint is paper. Photography stays natural (no tinting), soft and misty.
Fine paper grain.

## Animation curves & timing
- Softer than industrial: montage cuts every 5–7 frames, holds of 1–2 s; camera eases are long (`ease.camera`).
- Radiance grows with `ease.enter` over 20–30 frames, bars staggered by length; a disc matte opens with
  `ease.morph`. Bars rotate very slowly (≤0.15°/frame) while holding — the hold is alive.
- Tiles settle in a stagger (fast arrival, long settle), no bounce beyond `springs.leaf`.

## Transition vocabulary (the seven techniques, this style's way)
- **Seamless zoom** (signature): into a moodboard tile until it is the frame; out of another tile back to the board.
  Zoom about the fixed point so the tile grows straight into full frame with no drift.
- **Track matte:** a forest disc opens behind the growing bars and becomes the next scene (photo → poster).
- **Object continuity:** the mark keeps its position and size from the tile to the full-bleed photo to the poster.
- **Match cut:** sunburst poster → round enamel pin / round chip of the same radius (ref6 cut05).
- **Motion continuity:** bars' outward motion hands over to type rising from the bottom edge.
- **Push/pull:** slow 2–4 % push on photography while the radiance grows.
- **Perspective:** collateral laid on bark or tables at a slight angle; never a 3D fly-through.
- Rhythmic hard cuts on the beat are allowed in the montage section.
- Avoid: crossfades, glitch, neon glow, drone swoops, lens-flare sunrises.

## Camera
Natural and patient: push-ins on photographs, seamless zooms through the moodboard, 1–3 % breathing on holds.

## Lighting & texture
Soft overcast or low morning light, mist for depth; collateral lit like a still-life. Uncoated paper, embossing,
wood grain, moss texture; fine grain overall.

## Sound
Warm organic percussion (wood blocks, shakers, soft claps) at 100–115 BPM, an air/birdsong bed, a rising shimmer
under the radiance, a soft wooden knock on each montage cut.

## Do / don't
Do: real nature photography, mark in a consistent place, radiance as the motion idea, numerals in lime chips,
cropped serif headlines. Don't: tint photos green, overuse lime as a fill, cut faster than 4 frames, add glow,
stock-nature clichés.

## Motion grammar
Patient and alive. The big changes are continuous camera moves (20–30 frames, `ease.camera`): into a tile, back
out of another. Growth eases out (`ease.enter`) and never quite stops — bars keep turning ≤0.15°/frame, photos keep
pushing 1–3 %. One idea grows at a time over a slow ambient move. A single softer montage section cuts every 5–7
frames; nothing cuts faster than 4. Tiles settle with `springs.leaf`, never a bounce.

## Scene archetypes
- **A · Moodboard cluster** — photo tiles, lime/forest chips with numerals, an "Aa" tile, wood, the mark with a
  tiny swaying sun at its heart; mint space between.
- **B · Full-bleed photograph** — the zoomed tile is the frame; the mark sits where it sat in the tile; slow push;
  the name rises.
- **C · Radiance poster** — forest disc, lime bars radiating from the mark (mint now), a cropped serif headline
  along the bottom, one grotesk line top-left; it holds turning.
- **D · Collateral still** — type specimen, a numeral in a lime chip on forest, a lime field with a corner sunburst,
  a card on bark; 5–7 frames each in a montage.
- **E · Radiant lockup** — mark + name on forest or lime, the sun turning, one line; long breathing hold.

## Sequence model
opening (A, ~18 %) → immersion (B, ~17 %) → radiance (C, ~22 %) → montage (D, ~20 %) → resolution (E or back
to A, ~23 %). Carried by `tile-zoom`, `radiance-disc` (+ `rise-handover` into the headline), `soft-montage`,
`radius-match`, `tile-pullback`. Mostly continuous; the montage is the only run of hard cuts.

**Example mini-edit (20 s):** 1 tiles and chips settle around the Meadowline mark → *tile-zoom* → 2 the hills tile
becomes the frame, the name rises → *radiance-disc* → 3 bars grow out of the mark, the forest disc opens, "of
Growth" rises cropped → *soft-montage* → 4 specimen, 96 % chip, lime field, card on bark, then a 1 s hold →
*radius-match* → 5 the round chip becomes the sun of the lockup, "Capital that grows back", hold 2 s.

## Element behaviour
Serif headlines rise from the bottom edge and stay cropped; grotesk lines fade-rise; mono labels are static. The
mark never moves on its own — the world zooms and grows around it, and it flips lime ↔ mint as the disc passes.
Numerals live in chips that settle in a stagger. Photography is real and untinted, enters by zoom and breathes.
Shapes: rounded tiles, chips, discs that open as mattes, bars that grow and turn. No particles; fine paper grain.

## Audio relationship
Ambient and swell-driven: zooms and the disc ride long swells that start before the beat and bloom on it; montage
cuts on 8ths of a warm 100–115 BPM groove (wood, shaker, soft claps); holds sit on air and birdsong; a rising
shimmer under the radiance.
