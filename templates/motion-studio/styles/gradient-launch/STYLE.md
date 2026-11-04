# 10 · Gradient Launch

Tokens: `style.json`. Craft that applies to every style: `MOTION_RULES.md`. Reference evidence: the dark gradient
promo in `styles/_research/REFERENCES.md` and its frame-by-frame analysis in
`src/compositions/replicas/dark-promo/ANALYSIS.md`. Principles only; no frame is copied.

**Idea:** a launch film on a near-black stage, cut to the bar. Copy arrives as white lower-case words whose letters
hop into place from all over the frame on a stepped tick grid. Between the words, one warm gradient light
(orange → pink → violet) carries everything: a letter becomes a streak of light, the light flares inside a glass tile,
the tile becomes the product's glowing input pill, the send button fires rings, and the final word ignites in that
gradient while a giant dark echo of it slides past behind. Fast and confident, never busy.

## Visual grammar & composition
- One thing at a time, dead centre: a short word (~11 % of the frame height), one product object, or one giant
  gradient word cropped by the frame edge for a single beat.
- Near-black with a faint violet cast is the world. Colour exists only where the light is. A white page appears at
  most as a flash or a wipe.
- Rounded shape language: pills, capsules, circles, glass tiles (radius ≈ 22 %), small rounded squares; dashed
  outlines and dot rings for construction moments.
- No watermark, no corner handle, no creator tag. Ever.

## Typography
- Geometric grotesque, semibold, lower case, tight tracking (−0.02 em): Inter Tight 600 (SF Pro-like). 400 for typed
  prompts and UI copy, 300 for a thin gradient product name at the end.
- Words are short (one to three) and readable for 6–15 f before the next cut.
- Letters assemble by **hopping**: each glyph holds a position for 1–5 frames, then jumps with no interpolation to
  the next, each hop closer, until it lands. Small pink squares hop on the same grid and can stand in for a missing
  letter. Hopping is the style's deliberate frame language, not a lazy step.
- On a cut, a word may instead arrive with a 3 f tracking collapse (0.38 em → −0.02 em) and 50 → 100 % opacity.

## Colour & material
- Stage `#030106`, ink white, muted `#8D8A93`, UI surface `#1D1B21`, night `#0C0B10` for the echo.
- The brand gradient is built from three roles: `accent3` violet → `accent2` pink → `accent` orange (`accent4` blue at
  the cool end of rings). Recolour the roles and every gradient, glow and square follows.
- Gradients are light, never fills: per-letter gradient on a word, a flowing vertical gradient on the ignited word,
  a gradient border with a running light, rings, a flare's halo. Glows are soft and tied to the light source.
- Feature icons are round grainy spheres in palette gradients with white line glyphs.

## Animation curves & timing
- Two clocks. **Hops:** held frames and instant jumps on a shared tick grid (6–10 ticks a second). **Everything else:**
  on ones, expo-out on entry `[0.16,1,0.3,1]`, ease-in on exit `[0.55,0,1,0.45]`, in-out `[0.65,0,0.35,1]` for
  camera tracking and morphs. Never linear.
- Big early deltas that decay: the flare halo, the giant-word contraction, the dot rings closing. Rings expand almost
  constantly and slow a little, then fade.
- Presses answer in 2–4 f (0.93 → 1.03 → 1); typed characters bloom violet → pink → white and sharpen in ~6 f.

## Transition vocabulary (the seven techniques, this style's way)
- **Typographic — hop assemble / hop out** (signature): the letters are the transition.
- **Object morph — letter to light:** one stroke of a word stretches and tilts into a streak that flares.
- **Object morph — tile to pill:** the glass tile widens into the input pill; the flare core shrinks into the send button.
- **Seamless zoom — send pulse:** press, the pill collapses into the button, two gradient rings expand across the
  frame; the button becomes the next object.
- **Track matte — outline fill:** a dashed outline word contracts from ×2.4 and is filled by a sweeping band.
- **Spatial — dot rings:** rings of gradient dots close in around a word and counter-rotate.
- **Flash — ignition:** the white word jumps in scale and fills with flowing gradient as its dark echo slides past.
- **Rhythmic cut — bar cut:** hard cuts on the bar between centred words; squares keep hopping across the cut.
- Never a crossfade between words or a slow dissolve.

## Camera
Locked and centred most of the time. When it moves it means it: a push into the input pill, a track that follows the
typing caret, a pull-back on send, a giant word contracting to rest. No floating drift, no handheld.

## Lighting & texture
One warm gradient light that lives in the content (flare, pill border, rings, ignited word). A light shaft and a
floor glint around the glass tile, dust motes in the flare. Otherwise clean black; grain only inside icon spheres.

## Sound
Bright electronic around 120–125 BPM. Words land on beats, hops tick on hi-hats, the flare and ignition sit on bass
hits, send rings on a downbeat. SFX: ticks, a filtered whoosh, glassy key ticks, a click + shimmer, a sub hit.

## Do / don't
- Do: one word or one object at a time, hop letters, the gradient as light, real product UI in dark mode, a beat for
  every landing, short holds.
- Don't: watermarks or handles, crossfades, serif or condensed type, colour everywhere, flat gradient backgrounds,
  more than one hero light at a time, long holds with nothing alive.

## Motion grammar
- **Pacing:** fast, a new word or object every 0.8–1.5 s.
- **Rhythm:** stepped hops on a tick grid + smooth light and UI moves between them; cuts on the bar.
- **Easing:** expo-out in, ease-in out, in-out for tracking; hops have no easing at all.
- **Movement:** hop assemble, tracking collapse, streak, flare contraction, pill morph, typing bloom, press + rings,
  sphere slide-out, outline contraction + fill, dot rings, ignition + echo.
- **Density:** sparse; a few squares or confetti dots as punctuation.

## Scene archetypes
- **A · Hop word:** white lower-case words assembled from scattered hopping letters; pink squares.
- **B · Light tile:** a white-hot flare in a warm halo inside a frosted glass tile.
- **C · Product UI:** phone screen, glowing input pill with running light, typed prompt, send rings, result card, list.
- **D · Feature icons:** round gradient spheres sliding out from behind each other, typed labels, confetti.
- **E · Hero ignition:** outline word / dot rings → gradient ignition with a dark echo; the thin gradient name and mark.

## Sequence model
Opening (18 %, A) → letter to light → development (17 %, B) → tile to pill → escalation (30 %, C, D) → send pulse →
reveal (20 %, A/E) → ignition → resolution (15 %, E). Hard cuts on the bar between words, continuous morphs inside the
light/UI passages; 10–45 s.

**Example mini-edit (15 s AI music-app launch):** 1 · "every song starts" hops together (0–60) → bar cut → 2 · a
gradient "small", its l becomes a streak that flares in a glass tile (60–120) → tile to pill → 3 · the prompt types
in with blooming letters, send, rings, the result card (120–240) → send pulse → 4 · three feature spheres slide out
with confetti (240–315) → bar cut → 5 · "yours" as a dashed outline in closing dot rings, fills, ignites with its echo
(315–400) → 6 · the thin gradient product name and its mark drawn in light (400–450).

## Element behaviour
- **Text:** short, lower case, hops in; giant gradient fragments for one beat. **Logo:** drawn last as a glowing
  stroke that turns white. **Icons:** grainy gradient spheres. **Images:** rare, 4 f flickers or duotoned in the
  gradient. **UI:** the real app in dark mode; pill, button, card, list. **Shapes:** hopping squares, dashed outlines,
  dot rings. **Background:** near-black. **Particles:** confetti dots and dust motes.

## Audio relationship
Beat- and impact-driven: landings on beats, hops on hats, flare and ignition on bass hits, rings on a downbeat.
