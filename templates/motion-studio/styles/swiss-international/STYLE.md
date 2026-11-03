# 08 · Swiss International (International Typographic Style)

Tokens: `style.json`. Craft that applies to every style: `MOTION_RULES.md`. Reference evidence: none (no reference
video). Researched from the International Typographic Style (Basel/Zürich school, 1950s–60s posters and grid
systems): modular grid, objective photography and geometry, asymmetric flush-left layouts, sans-serif grotesk,
red/black/white. Principles only; no specific poster is copied.

**Idea:** the grid is the story. Every element sits on a visible modular grid, and motion is the grid
re-arranging itself: blocks slide along module lines, bars fill rows or columns, type rises from a baseline.
Clarity over decoration; information is the image.

## Visual grammar & composition
- A strict modular grid (12 columns × 6 rows, 80 px margins, 20 px gutters at 1080p) drawn as 1 px hairlines on
  both edges of every column and row, column indices in the top margin. The grid stays on screen.
- Asymmetric layouts: one large block of colour, one huge word or number, one small information column. Plenty of
  empty modules. Everything aligns to a column edge; nothing is centred.
- Objective geometry: circles, squares, bars and rules sized in whole modules. Photography, if used, is objective
  (architecture, objects), cropped to modules, black-and-white or in a single red duotone.

## Typography
- Neo-grotesk only: Archivo (800/900) for display, Hanken Grotesk for text (Helvetica/Akzidenz feel; never Inter
  Tight). Sentence case, tight display tracking (−4 to −5 %), optical left alignment (pull the cap a few px out).
- **Flush left, ragged right**, never justified, never centred. Hierarchy by size and weight jumps (26 px text vs
  300–700 px display), not by colour variety.
- Numbers are heroes (dates, counts, module sizes). German/neutral demo copy is fine; real institutions are not.

## Colour & material
- Off-white paper `#F3F1EC`, ink `#111111`, signal red `#E30613`, grey `#8A8883` for secondary text only.
- Flat colour, no gradients, no shadows, no texture. Red is used in large confident blocks or as a single rule.

## Animation curves & timing
- Decisive: `move` is a strong in-out ([0.8,0,0.2,1]), entries ease out fast, exits ease in. No bounce.
- **Snap to modules:** positions and sizes interpolate in whole grid units (rounded per frame), so blocks travel
  column by column. Bars stagger 1–2 frames per row/column, 6 f each.
- On ones; tempo metronomic (holds of 20–30 f between moves).

## Transition vocabulary (the seven techniques, this style's way)
- **Track matte — bar wipe on the grid** (signature): black/red bars extend row by row (or column by column) across
  the modules, the layout changes underneath, the bars retract in the same direction. Gutters stay visible as
  white lines through the bars.
- **Object continuity — modular slide:** a block resizes and travels along grid lines into its place in the next
  layout; a red circle squares into a red band module by module.
- **Push:** whole layouts push one module-width at a time, never a free pan.
- **Match cut:** a number becomes a module count, a circle becomes a dot of the next layout, a rule becomes a bar.
- **Motion continuity:** a bar wipe runs in the direction the previous block left.
- **Seamless zoom:** a straight scale into one module that becomes the next page (scale only, no blur).
- **Perspective:** none — the page stays flat; that is the point.
- Avoid: crossfades, rotation, 3D, blur, glow, texture, centred type, bouncy easing.

## Camera
No camera. The frame is the page; motion happens in its plane along the grid.

## Lighting & texture
None. Flat, objective colour on paper. The grid hairlines are the only texture.

## Sound
Dry and precise: a clean tick per module step, a short filtered swipe per bar, a low thud when a block lands;
minimal, metronomic music (or silence).

## Do / don't
Do: visible grid, flush-left ragged-right grotesk, huge numbers, red/black/white blocks in whole modules, bar
wipes, snapping moves, empty modules. Don't: centred or justified type, gradients, shadows, rounded UI,
decorative motion, more colours, perspective, illustration-as-decoration.

## Motion grammar
Metronomic and quantised. A move (6–20 f, strong in-out), then a hold (20–30 f) where the layout reads as a
finished poster, then the next move, about one layout per bar of music. Positions and sizes step in whole modules;
bars stagger 1–2 f per row or column. Hard acceleration, clean stop on the module, no drift, no settle. One or two
blocks move at once; the grid never moves.

## Scene archetypes
- **A · Poster** — a red block of whole modules, a huge flush-left word on the bottom rows, a small info column.
- **B · Number plate** — a black half-page with a red circle, a huge number flush left in the other half, a red
  rule and a unit label.
- **C · Module layout** — blocks re-arranged (a red band carrying a word, a 4 × 2 black module), body text in
  ragged-right columns.
- **D · Data grid** — bars or squares in whole modules growing module by module, the red one last, labels beneath.
- **E · Colophon** — the name flush left on a column edge, one rule, a small block of details; mostly empty.

## Sequence model
opening (A, ~18 %) → development (B, ~22 %) → explanation (C/D, ~25 %) → reveal (D, the red bar last, ~15 %) →
resolution (E, ~20 %). Wipes and modular slides replace cuts (presets in `style.json` `transitionPresets`:
bar-wipe-rows, bar-wipe-cols, modular-slide, module-push, number-match, module-zoom).

**Example mini-edit (15 s):** 1 "Raster" rises flush left, red block top right, the info column sets →
*bar-wipe-rows* → 2 the number plate: black half, red circle, "12" ("Spalten") → *modular-slide* → 3 the circle
squares into a red band carrying "Form", the black half shrinks to a 4 × 2 module with "08", three text columns
set → *module-push* → 4 six bars step up, the red one last → *bar-wipe-cols* → 5 colophon: "Raster und Form", a
rule, "Halle 8 · Di–So 10–18"; hold.

## Element behaviour
Text rises from a baseline mask on a column edge and drops back behind it (or goes under a bar). The logo is set
like type, never animated as an object. Icons are pure geometry in whole modules and step into place. Images are
objective, cropped to modules, revealed by a bar wipe. UI is redrawn as modules and rules. Shapes slide and resize
along grid lines. The background is flat paper with the visible grid. No particles.

## Audio relationship
Metronomic: every move starts on a beat and lasts whole subdivisions; module steps tick on 16ths, each bar is a
short filtered swipe, a block landing is a low thud, holds last one bar. Minimal electronic music or silence.
