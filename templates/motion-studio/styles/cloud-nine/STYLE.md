# 12 · Cloud Nine

Tokens: `style.json`. Craft that applies to every style: `MOTION_RULES.md`. Reference evidence: ref11 in
`styles/_research/REFERENCES.md` (one continuous 5.4 s square piece). Principles only; no frame is copied.

**Idea:** a daydream in one painted landscape. A bright sky with soft clouds over a rolling green hill, petals
drifting. White rounded letters ride a breeze onto the line, the word folds into a stack of floating app cards
that hover and tilt in 3D, and everything leaves by drifting out of focus while the camera rises into open sky.
Light, friendly, unhurried. One continuous shot.

## Visual grammar & composition
- One subject at a time (a word, a line, a card stack), slightly above centre. The hill holds the bottom third;
  the rest is sky.
- The world never cuts: the camera drifts, tilts up into sky and back down to the hill.
- Lots of air. Never more than one line of type or one stack of cards on screen.

## Typography
- Manrope 800 for hooks (letters wave in), DM Sans 500 for lines (words blur in). White, sentence case, tight
  tracking, a soft shadow (very low opacity, large blur) for legibility on light sky.

## Colour & material
- Sky `#36B4F5` → `#5AC5F7` → haze `#D0EAF9` at the horizon; clouds white with soft edges; hill `#A9D663` with
  `#6FBF3A` shade and tiny white/yellow flower flecks.
- Cards white with thin white borders and soft, diffuse shadows; one card may be dark (`#16181C`) for a photo or
  dark screen; small bright accents (green `#2FC25B`, blue `#2E6BE6`) inside screens.
- Drawn, not photographed: gradients and soft shapes. No hard flat blocks, no dark backgrounds.

## Animation curves & timing
- On ones, soft. Arrivals `enter` [0.22,1,0.36,1]; hovering `float` [0.37,0,0.63,1]; the camera `camera`
  [0.4,0,0.2,1]; exits `exit` accelerate out.
- Letters land with a gentle spring 2–3 f apart; words 4–6 f apart.
- Nothing fully stops: floating objects bob 2–6 px and sway 1–3°.
- Depth of field: things arrive from blur and leave into blur (0 ↔ 12–24 px).

## Transition vocabulary (the seven techniques, this style's way)
- **Typographic — wave in** (signature): letters fly along a curve, rotating and scaling up, onto the baseline.
- **Object morph — squash into cards** (signature): the word squashes into a white block that becomes the front
  card of a 3D stack.
- **Spatial — focus whip** (signature): the subject sweeps sideways and toward camera out of the focal plane.
- **Seamless zoom — sky drift:** the camera tilts up, hill sinking, clouds parallaxing, into open sky.
- **Typographic — blur in words:** words fade in from blur one by one; a new line replaces the old in place.
- **Motion continuity — card swap:** a card stays put while its screen changes.
- **Match:** the word's bounding box and the front card share a size and place.
- Never a hard cut.

## Camera
A floating camera in one world: slow drift, a tilt up into sky between ideas, a slight dolly toward the cards.
Depth of field does the cutting.

## Lighting & texture
Midday sun upper left with a soft glow in the corner; even, soft light on cards; diffuse shadows beneath them.
Grain barely there.

## Sound
Light and airy: plucks per letter, bells per word, soft whooshes for cards and whips, a warm pad, breeze and a
little birdsong.

## Do / don't
- Do: one subject, lots of sky, soft blur in and out, gentle floating, rounded everything.
- Don't: dark backgrounds, hard cuts, glitch or pixel effects, dense UI walls, harsh shadows, linear moves.

## Motion grammar
- **Pacing:** calm, an idea every 1.5–2.5 s, carried by floating moves.
- **Rhythm:** letter by letter, word by word, like a melody.
- **Easing:** soft arrivals, gentle springs, smooth float; never snappy.
- **Movement:** wave-in letters, word squash, 3D card stack hovering, screen swaps, focus whips, sky tilt,
  blur-in words, drifting petals.
- **Density:** low.

## Scene archetypes
- **A · Wave word:** a short hook waving in letter by letter over the hill.
- **B · Floating card stack:** three rounded app cards fanned in 3D, hovering, screens swapping.
- **C · Open sky:** a breath; the camera rises into clouds.
- **D · Breath line:** one line of text blurring in word by word in open sky.
- **E · Hill lockup:** the app icon on a rounded tile floats down above the hill, the name under it.

## Sequence model
Opening (20 %, A) → squash into cards → development (30 %, B) → focus whip → escalation (15 %, C) → blur in →
reveal (20 %, D) → sky drift → resolution (15 %, E). One continuous shot, 5–20 s.

**Example mini-edit (8 s app teaser):** 1 · "Plan less" waves in over the hill (0–48) → squash → 2 · a white
card with a calendar and photo card fan out, the front screen swaps to a timer (48–120) → whip → 3 · the stack
whips out of focus, the camera tilts into clouds (120–144) → 4 · "Your week, sorted" blurs in, then "without the
stress" (144–204) → drift → 5 · back on the hill, the app icon floats down, the name blurs in (204–240).

## Element behaviour
- **Text:** white, short, sentence case; hooks wave in, lines blur in. **Logo:** on a rounded tile floating above
  the hill. **Icons:** rounded tiles that bob. **Images:** inside cards only. **UI:** real screens on rounded cards
  in a 3D stack. **Shapes:** rounded only. **Background:** the painted sky and hill, moved only by the camera.
  **Particles:** a few drifting petals.

## Audio relationship
Melodic and light. Letters and words on notes or soft hats; the stack on a phrase start; the focus whip on a
swell. SFX: plucks, bells, soft whooshes, breeze.
