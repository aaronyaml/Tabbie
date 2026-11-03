# 06 · Kinetic Type (hand-drawn logotype animation)

Tokens: `style.json`. Craft that applies to every style: `MOTION_RULES.md`. Reference evidence: ref1
(`styles/_research/REFERENCES.md`: sheet, bursts at 4.08 s / 6.92 s / 13.21 s — radial streak burst, debris
assembly, glyph substitutions, lightning exit).

**Idea:** the word is a character. It is born from a burst of energy, assembled from scraps, it fidgets while it
holds, and it leaves with a bang. Cel animation energy, three inks, one heavy rounded wordmark. Every frame looks
drawn, never tweened.

## Visual grammar & composition
- Near-black ground (`#09080E`) with grain; the word centred, big, alone. Negative space is the black sky.
- Bursts then holds. Bursts fill the frame edge to edge (streaks, debris); holds are a single centred word with
  a few sparks around it.
- One glyph is the playground (ref1: the "a"). It keeps cycling substitutions while the rest of the word holds:
  a tilted tile with the letter knocked out, a spiral, a four-point star, a blob in the second ink.
- Shapes vocabulary only: four-point stars, diamonds, squares, spirals, zig-zags, scribble circles.

## Typography
- One ultra-heavy rounded display (Bagel Fat One / Shrikhand / Cooper-weight), **lowercase**, tight, set huge.
  Support text (rarely needed) in Bricolage Grotesque.
- Letters land individually with a squash (1.28/0.70 → 0.90/1.14 → 1.04/0.97 → 1) over three drawings.
- Letters boil: ±1.5 px and ±1° jitter per drawing plus a small edge displacement. Never perfectly still.

## Colour & material
- Exactly three inks on black: chalk `#F4EFE3`, signal yellow `#F5B700`, ultramarine `#3A2FE0` (+ a dark grey
  `#3B3946` for depth streaks). Flat fills, no gradients except the star's soft glow.
- Dry, broken brush edges: displacement noise on every stroke, re-seeded per drawing.
- Yellow is the energy colour (tiles, lightning), ultramarine the shadow/offset colour (the lightning's echo).

## Animation curves & timing
- **On twos** (`step: 2`, `stepFrame`): the picture changes every 2nd frame. Holds are still alive (boil, sparks,
  the playing glyph).
- Snap-in eases with overshoot (`enter` y > 1), anticipation on exits (`exit` dips below 0), stepped morphs
  (`steps:3`) for substitutions. Springs are under-damped (`letter`, `glyph`): cartoon bounce is allowed here.
- Typical beats at 30 fps: circle → star 10 f, burst 30–40 f, assembly 2 f stagger per letter, hold 40–50 f with a
  substitution every 6 f, exit 20 f.

## Transition vocabulary (the seven techniques, this style's way)
- **Seamless zoom — hyperspace burst** (signature): a lens-star blows open into radial brush streaks rushing from
  centre; the zoom-through *is* the arrival of the next scene.
- **Object continuity — debris assembly:** the burst's shapes fly back in and snap into glyphs; the same shapes
  later become substitutions of one glyph.
- **Match cut on shape:** scribble circle → star → the "o"; tile → letter → spiral. Round things become round things.
- **Track matte — stroke wipe:** a speed line slices the word, then a zig-zag lightning eats it left to right (the
  word is clipped behind the stroke's tail).
- **Motion continuity:** exits leave in the direction the energy travels (lightning left → right, last star drifts
  on to the next mark).
- **Push/pull:** done with drawn speed lines and streak density, not a camera move.
- **Perspective:** fake only (streak length and width grow with distance from centre); no 3D tilt.
- Never: crossfades, opacity fades of the word, blur tweens.

## Camera
Locked. The drawing does the camera work (radial streaks = zoom, speed lines = pan). A one-pixel boil on holds.

## Lighting & texture
Graphic, not lit. Grain re-seeded per drawing (`texture.grain`), faint vignette, a soft chalk glow only around
lens-stars.

## Sound
Cartoon foley locked to drawings: rising whoosh into the burst, pops and ticks as debris lands, a small boing per
squash, crackle-zap on the lightning, a sparkle ting on the last star. Music: punchy, beats land on the snaps.

## Do / don't
Do: three inks, lowercase heavy rounded type, on twos, a glyph that plays, bursts then living holds, stroke wipes,
end on a twinkle. Don't: photography, gradients, glass, smooth easing on ones, more inks, fades, a dead hold,
realistic 3D camera moves.

## Motion grammar
On twos, always: every drawing holds 2 frames, every snap lands on an even frame and on the beat. Bursts then
living holds: 0.6–1.3 s of edge-to-edge chaos, then 1.3–2 s of one word that fidgets (boil, one playing glyph,
sparks on the hi-hats). No slow-ins: things arrive in 2–4 drawings and squash; exits wind up for one drawing and
leave in 2–3. Density swings between extremes (30+ drawn elements in a burst, one word in a hold), never in between.

## Scene archetypes
- **A · Hyperspace arrival** — a boiling scribble circle collapses into a lens-star; the star flares and blows open
  into radial brush streaks edge to edge.
- **B · Debris assembly** — diamonds, blocks and sparks fly in from every edge and snap into the letters, 2 f apart,
  each landing with a squash.
- **C · Living hold** — the word huge and centred; one glyph cycles tile → spiral → star → blob every 6 f; boil and
  sparks keep it alive.
- **D · Word swap on the beat** — a short line one word per downbeat; the round glyph stays put as the anchor while
  the words change around it.
- **E · Lightning sign-off** — a speed line slices the word, a zig-zag bolt eats it left → right, one star is left.

## Sequence model
opening (A, ~15 %) → development (B + C, ~25 %) → escalation (D, ~25 %) → reveal (a second burst, B + C on the
name, ~20 %) → resolution (E, ~15 %). Changes are drawn, never dissolved (presets in `style.json`
`transitionPresets`: hyperspace-burst, debris-snap, shape-match, impact-frame, stroke-wipe, star-carry).

**Example mini-edit (10 s):** 1 a scribble collapses into a lens-star and bursts → *debris-snap* → 2 debris snaps
into "make", the "a" starts flipping → *impact-frame* (one yellow drawing on the downbeat) → 3 "some", "noise" on
the beat, the "o" holds its place → *shape-match* → 4 that "o" becomes the "o" of "kazoo", the wordmark snaps
around it and plays → *stroke-wipe* → 5 lightning eats "kazoo", one star twinkles and drifts to centre.

## Element behaviour
Text arrives from debris and leaves by being eaten; it never fades. The logo is assembled, plays during its hold
and signs off with lightning and a star. Icons exist only as drawn shapes that double as glyph substitutions.
No photography or UI; if a product must appear, redraw it as flat ink. The background stays grainy black (the only
colour field is a one-drawing impact frame). Particles are drawn sparks and debris that become letters.

## Audio relationship
Beat- and impact-driven cartoon foley: snaps, impact frames and word swaps on downbeats, the burst on a riser into
the drop, sparks on hi-hats, the lightning on the last hit; pops per debris, boing per squash, crackle-zap, a
sparkle ting to close.
