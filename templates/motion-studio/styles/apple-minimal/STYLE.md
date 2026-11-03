# 01 · Premium Minimal (Apple-inspired)

Tokens: `style.json`. Craft that applies to every style (philosophy, continuity, tokens-first, interaction physics,
audio sync, review): `MOTION_RULES.md`. Reference evidence: ref4 (`styles/_research/REFERENCES.md`).

**Idea:** the product is the hero on a quiet stage. Restraint, clarity, precision, excellent type, tactile
interaction, realistic depth, excellent easing and intentional negative space. Apple's *principles*, never its
branding.

## Visual grammar & composition
- **Start from white** (`#FFFFFF` `#FBFBFB` `#F7F9FA` `#F5F7F8`) with near-black type (`#0A0A0A`, secondary
  `#666`/`#7A7A7A`). Never go dark because the brand's UI is dark.
- **One focal point**, centred or on a strong third, with lots of negative space; empty is intentional.
- **Black ↔ white switches** are allowed as a deliberate beat (a single word on black, then the white set returns) —
  ref4 opens on black type and lands on white UI.
- **Full-screen app exception:** UI may fill the frame only when the viewer visibly *enters* it — device on white
  → camera push → bezel leaves frame → native UI (dark/brand colour allowed). Leaving: the UI contracts back into
  its device/card/window and the white set returns.

## Typography
- The brand's own font first; else a neutral grotesk (Inter Tight / SF-like) — the Apple influence is composition,
  not swapping a brand's font for SF.
- Sentence case, tight tracking on display (−2 to −4 %), few weights, short confident statements.
- **Kinetic captions:** word by word on the beat; a key word sits in an accent **pill** (ref4: "track
  conversions", "Track"); a pill can grow from the text caret.

## Colour & material
- Brand colour is an accent: key words, pills, buttons, indicators, a light bloom. Never a full-frame fill
  (except the bold full-screen-app beat).
- **Premium gradient light (optional):** only if the brand colour falls in a family — Solar Flare `#FF5A1F
  #FF2E63 #FFB020 #FF8A00`, Ultraviolet `#3A1CFF #FF3EC8 #8A2BFF #5CE1FF`, Glacier `#2F6BFF #6FD3FF #B9F3FF #8C7BFF`,
  Lime Soda `#00B8FF #2BE0A0 #C6FF3D #FFE53B`. It looks like coloured light through glass (large diffused fields,
  blurred mesh, bloom, refraction), never a CSS `linear-gradient`. White still dominates.
- **Glass as accent**: subtle translucency, background blur, thin highlights. Shadows wide, diffused, low opacity.
- **Devices & 3D**: premium product-render quality (real proportions, glass, metal, studio light; `iPhone3D`); no
  rotating flat PNGs.

## Animation curves & timing
- Snappy first, smooth second: taps 80–200 ms, small UI 120–250 ms, major transitions 300–700 ms; cinematic camera
  only when the composition needs it.
- Enter = ease-out (`[0.22,1,0.36,1]`), exit = ease-in, state change = ease-in-out. Springs with 2–5 % overshoot on
  buttons, cards, icons, toasts (`springs.button/card/logo`); never cartoon bounce.
- On ones. Motion blur from velocity only.

## Transition vocabulary (the seven techniques, this style's way)
- **Object continuity — container transform** (signature): an inline chip, card or icon grows into the next
  scene (ref4: the "22" chip inside a sentence becomes a rounded video window). Layout interpolates; contents swap
  while it moves fast.
- **Push-in / pull-back** through floating UI layers; app expansion to full frame and back.
- **Seamless zoom** into a number, row or button that becomes the next screen (`logZoomAt`).
- **Type assembly:** letters of the next line arrive scattered in depth with blur and settle into the word.
- **Matte**: a pill or rounded rect is the window into the next scene. **Match cuts** on shape (circle → dot →
  logo). **Perspective**: tilt the canvas 5–15° for depth, keep one vanishing point.
- Avoid: crossfades, glitch, unmotivated hard cuts.

## Camera
Shallow 2.5D: slow push-ins on floating UI, gentle parallax, canvas tilt for depth, long settles (`ease.camera`).
Never handheld, never constant zoom.

## Lighting & texture
Clean, no grain. Soft studio key, contact shadows, coloured light bloom rising from the frame edge on key beats
(ref4's green glow), glass refraction on accent moments.

## Sound
Tactile and restrained: compression clicks, soft settles, air sweeps peaking at seams, a small confirmation chime;
natural VO; music with space. No TikTok whooshes or gaming sounds.

## Interaction (this style leans on it most)
Every press animates the button (1.00 → 0.96–0.98 → 1.01 → 1.00); the cursor moves on curved paths, pauses
before a click and compresses on it (`AnimatedTouch`); toasts and rows spring in; nothing interacted with is dead.

## Do / don't
Do: white + black + one accent, tokens first, physical interaction, velocity blur, container transforms, negative
space. Don't: dark by default, full-frame brand colour, decorative gradients, generic SaaS/template looks, fake
HUDs, gratuitous glass, constant zooms, crossfades.

## Motion grammar
Smooth, on ones. Calm scene tempo, snappy UI response: input answers in 80–250 ms and then settles long. One idea
per beat, 1–3 things moving at once, everything else holding. Springs only on what's touched (buttons, cards,
toasts), 2–5 % overshoot. The camera never cuts; it pushes, tilts and settles.

## Scene archetypes
- **A · Kinetic caption** — one line on white, words from depth blur behind a caret, the key word in an accent pill.
- **B · Floating hero UI** — one glass card or device, real UI, numbers count and charts draw inside it.
- **C · Depth stack** — the canvas tilts; the hero card in front, 2–3 satellites at other depths by parallax.
- **D · Interaction beat** — cursor on a curve, pause, press; the control compresses; a toast springs up.
- **E · End lockup** — the last container resolves into the mark; one line; accent bloom; hold.

## Sequence model
opening (A, ~15 %) → development (B, ~30 %) → escalation (C/D, ~20 %) → reveal (B/D + bloom, ~15 %) →
resolution (E, ~20 %). No hard cuts: one container and camera chain carries every change (presets in
`style.json` `transitionPresets`: container-transform, tilt-reveal, push-through-ui, fold-home, type-assembly).

**Example mini-edit (20 s):** 1 caption types "Everything your team ships, in one view." → *container-transform*:
the pill becomes the dashboard → 2 hero UI: 12,480 counts, the chart draws → *tilt-reveal* → 3 depth stack:
satellites float in → *push-through-ui* to Share → 4 interaction: press, toast, green bloom → *fold-home* →
5 end lockup: the pill becomes the mark, one line, hold 1.5 s.

## Element behaviour
Text word by word from depth blur, leaves by drifting back. Logo resolves out of the last container and springs.
UI animates inside its card (counters, charts, rows); every press compresses. Images: real product UI only.
Shapes are pills and rounded rects that become windows. Background stays quiet; the accent arrives as light.
No particles.

## Audio relationship
Tactile and VO-friendly: scene changes on phrase starts, UI hits on light percussion, air sweeps peaking at the
seams, one confirmation chime; space left for voiceover.
