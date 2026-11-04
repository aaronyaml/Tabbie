# 11 · Sky Meadow

Tokens: `style.json`. Craft that applies to every style: `MOTION_RULES.md`. Reference evidence: the sky-cards
replica (`src/compositions/replicas/sky-cards/ANALYSIS.md`, from `references/replica-sky-cards.mp4`, one 5.4 s
piece). Principles only; no frame is copied, and no creator handle or watermark from the reference is used.

**Idea:** a sunny app teaser shot through a real lens. A painted summer sky over a lime meadow is the whole world;
bold white type and glossy app cards float in it. The type is physical: extruded, it springs up letter by letter
and collapses into a point, and the cards burst out of that point. The camera is real: it racks focus, tilts with
the action and whips through a blur-and-cyan exposure into a new framing. Everything feels light, fresh and
bouncy, but never cartoonish.

## Visual grammar & composition
- The plate: a painted sky (deep sky blue at the top, paler cyan toward the horizon) with cumulus banks low on the
  left and right, over a rolling meadow whose horizon sits low (lower 15–35 % of the frame) and slopes down to the
  right. A soft white sun bloom in the top-left corner.
- One subject at a time floats in the sky: one line of type, or a fan of two or three cards in the centre.
- Cards: rounded squares (≈14 % corner radius) with thick white frames and soft blue-grey shadows, tilted on a
  fan (back-left −10°, middle +3°, front-right +12°), overlapping by a third.
- After the cut the frame is calmer: a deeper blue sky, the clouds lower, one centred line.
- Small butterflies and petals drift through the opening, defocused. Nothing in the corners: no logos, handles or
  watermarks.

## Typography
- Two voices. **Display:** Inter Tight 700, tight (−0.055 em), sentence case, white with a grey-blue extruded
  underside (four 1 px steps from white to `extrude`) and a soft blue cast shadow. Big: cap height 9–12 % of the
  frame height. **Text:** Inter 500, −0.025 em, white with a soft white glow, ≈7 % of frame height, centred.
- Display type snakes on letter by letter; text type fades up word by word. Never set long copy: one line each.

## Colour & material
- Sky `#3DB8F6` → `#A6E6FF`, cloud white, meadow `#93D83C` / `#4E9F25`, white ink and card frames, extrude
  `#B4C4D8`, one dark card `#0E1420`, accents green `#2BC84A` (an icon) and blue `#2F86FF` (a knob, a gauge),
  exposure cyan `#6EE8FF` for the whip.
- High-key and saturated; contrast comes from white on blue and one dark card. Brand colours go onto the accents
  and the card content; the sky stays a sky (a brand can tint it, but keep it light and airy).

## Animation curves & timing
- On ones, with velocity blur on anything fast (cards, the whip).
- Letters: a spring (stiffness 210, damping 14) plus a sine bump, so each letter overshoots its slot (scale ≈1.3,
  lifted) and settles; stagger 1.25 f, so the tail is still oversized while the head has settled.
- Cards: fast ease-out emergence (most travel in 4–6 f), long settle, slow drift, then an accelerating ease-in exit.
- Text lines: ease-out fades from below (6 f per word, 3 f apart); blur-morphs ease in and out.
- Camera: ease-in-out tilts and pushes; rack focus 12 f ease-out.

## Transition vocabulary (the seven techniques, this style's way)
- **Typographic — letter snake** (signature): letters are born small below-right of their slots and spring up.
- **Object morph — collapse to burst** (signature): the settled line rises and converges on its centre under blur
  while the cards burst out of that point.
- **Flash — exposure whip** (signature): everything accelerates down-left with blur, the plate pushes in and blooms
  cyan-white, a hidden cut lands on a new framing that settles out of the blur and exposure.
- **Object morph — blur morph:** a line condenses under blur and the next line resolves from blur with wide word
  gaps that close.
- **Spatial — rack focus:** scenes open and can close on a soft, hazy plate.
- **Motion continuity:** cards carry the camera's tilt and the whip's direction; the new framing settles from the
  same direction.
- **Match cut / track matte:** a card's screen can hold the same sky as the plate, so pushing into it continues the
  world. Never a plain crossfade or a visible hard cut.

## Camera
A real lens on a painted plate: rack focus in at the start, a slow tilt up while type plays, one big ease-in-out
tilt and push (the meadow going soft) when the cards rise, a whip push into the cut. After the cut: a gentle drift,
a small dip and push as the closing title grows. Depth of field follows the subject.

## Lighting & texture
High summer daylight from the top-left, a soft sun bloom, an airy cyan haze tied to focus. Clean: no grain.

## Sound
A light, bright beat bed under a friendly voice-over; letters land just ahead of their words, the burst rides a
bass hit, content swaps sit on beats, a whoosh peaks on the hidden cut.

## Do / don't
- Do: one subject at a time, springs that settle, staggered ripples, real focus and blur, a lot of sky.
- Don't: dark grounds, flat linear moves, visible hard cuts, more than three cards, dense copy, cartoon bounce on
  UI content, watermarks or handles on cards.

## Motion grammar
- **Pacing:** brisk; a new action every 0.5–1.5 s, holds of 10–20 f once something settles.
- **Rhythm:** springy, staggered ripples (letters 1.25 f, cards 3–4 f, words 3 f apart).
- **Easing:** springs and strong ease-out in, ease-in out with blur, ease-in-out camera; never linear.
- **Movement:** letter snake, collapse, card burst and fan, live content swaps, drift, words fading up, blur-morph,
  rack focus and push-in.
- **Density:** low to medium; the sky always has room.

## Scene archetypes
- **A · Letter line:** the hook in big extruded type; rack focus in, letter snake, collapse.
- **B · Card burst:** two or three app cards burst from the word and fan; the camera tilts with them.
- **C · Live card:** one card's content changes (wipe, slide, morph) while it drifts.
- **D · Sky line:** one centred line of lighter type over a deeper sky; words fade up, blur-condense out.
- **E · Lift title:** the closing title blur-morphs in with closing gaps and rises, growing.

## Sequence model
Opening (20 %, A) → collapse to burst → development (35 %, B then C) → exposure whip → reveal (25 %, D) → blur
morph → resolution (20 %, E), optionally rack-focusing out to loop. Nearly one continuous shot: one hidden cut
under the whip per 5–8 s. Typical length 5–20 s.

**Example mini-edit (6 s photo-app teaser):** 1 · the sky racks into focus and "Light as air" snakes on (0–32) →
collapse → 2 · three cards burst out and fan while the camera tilts up (26–70) → 3 · on the beat the lock screen
wipes to a stats view, the album's photos slide for a new one, the widget icon morphs into a gauge (52–96) →
whip → 4 · a deeper sky settles and "Every photo, sorted for you" fades up (96–146) → blur morph → 5 · "Try it
free" sharpens, its gaps closing, and rises (140–180).

## Element behaviour
- **Text:** display type snakes on letter by letter; text type fades up word by word; both leave by blur.
- **Logo:** on a card or as the closing word, never a corner bug. **Icons:** round, pulse and morph into widgets.
- **Images:** the plate is the world; product photos live inside cards as tilted stacks that slide and swap.
- **UI:** glossy rounded cards, white frames, soft shadows; content changes live; three at most.
- **Shapes:** rounded squares, pills, rings, arcs. **Background:** the painted plate, racked and tilted.
- **Particles:** a few butterflies and petals, defocused, in the opening only.

## Audio relationship
Beat-driven and VO-locked: letters lead their words by 1–3 f, the burst sits on a bass hit, swaps on beats, the
whoosh peaks on the hidden cut, the closing rise rides the last phrase. SFX: soft ticks, a bassy pop, small UI
clicks, an airy whoosh, a soft swell.
