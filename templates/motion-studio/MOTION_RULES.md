# Motion rules: the craft

The product is the hero; motion explains it. These rules hold in every style; the look comes from the film's style.

## Decide before you animate
For every shot, hit and transition, know: what it says, where the eye goes in and out, what the viewer
should feel, and why it moves. Movement without a reason is removed. Stillness is a choice.

## Story
- One sentence the film proves. Setup → reveal → payoff → end card (name + one line).
- One peak per section; a calm stretch before every peak, or the peak won't read.
- 15–30 s for a social spot, up to 60 s for a launch film.

## Look
- The film's style decides the look (see Style below). In every style: real product UI, sharp and large, never
  invented screens or a redesigned product; type readable, few words per card (≤ 6 on screen at once).

## Motion
- Ease with the style's curves and springs (style.json `motion`); stepped timing (on twos, `steps:N`) only when
  the style calls for it; never plain linear except continuous drifts.
- Camera: slow push-ins and gentle parallax; one move per shot. Shake only on a genuinely heavy hit.
- Text arrives in one gesture (fade + small rise or a mask wipe), holds long enough to read twice, leaves quietly.
- At most three flashes per second, and few at all. Big hits are rare.

## Style
Every film is made in one style from the library in `styles/` (`styles/index.json`; each style has `style.json`
tokens and a `STYLE.md` motion language: look, type, colour, curves, timing, transitions, camera, texture, sound).
The style overrides any generic look or motion default here; the craft rules (decide before you animate, story,
continuity, music sync, checks) still hold.
- Use the style in the film's `project.json` (`"style": {"id": …}`). When it's `auto` or missing, read the brief,
  the brand and any references, compare at least three distinct styles, pick one and record the pick, the reason
  and the runners-up. Never fall back to one style silently.
- Brand colours and fonts map onto the style's colour roles (bg, ink, accent …); the style's colour logic stays.
- A blend borrows palette, type or texture from a second style; the base style's motion stays in charge.
- Changing the style keeps the content (brief, script, scenes, audio); only the look and motion are redrawn.
- Reference videos are analysed visually: extract frames (contact sheet, shot starts, frames around every cut,
  consecutive-frame bursts inside shots), look at them, and write what makes the look and motion distinctive,
  which frames you inspected and what frames can't show.

## Transitions: one continuous film
The film plays as one choreographed move through one world: scenes never just appear and disappear.
- Plan the transitions before the scenes: for every scene change, the technique, the element that carries the eye
  across (the bridge), what carries over (direction, speed, scale, position, colour, meaning) and the hand-off frame.
  Then build each scene so its last frame sets up the bridge.
- Reach for, in order of fit: a camera push-in or pull-back that reveals the next scene; a matte reveal (type, a
  shape or a UI element becomes the window into the next scene); a match cut (same shape, position, scale, colour
  or meaning, morphing where they differ); object continuity (an element expands, collapses or moves into the next
  scene); motion continuity (direction, speed and rhythm carry through); a perspective shift (2.5D/3D depth,
  parallax, a plane turning into the next); a seamless zoom into a detail that becomes the next scene.
- Hide the seam inside the motion: the next shot starts moving before the last one stops (a few frames of overlap),
  one easing curve runs through the join, blur follows speed and peaks at the seam. Zoom in log scale, so it feels
  like one constant move.
- Never by default: abrupt cuts, plain fades or crossfades, preset wipes. A hard cut is fine when the rhythm, the
  story or an impact calls for it; say why in the plan.
- One transition family per film (the style's vocabulary), two or three signature moves, the biggest at the story peak. Type stays readable
  before and after every move.

## Music
- Place scene changes (and the occasional deliberate hard cut) on downbeats and phrase starts; save effects for drops and the sounds that stand out.
- Anchor every synced moment to the analysis (see CLAUDE.md). Visual hits land on the frame of the sound, never early.
- End on the music's last hit or a held chord, not mid-phrase.

## Check before you call it done
Watch it through at full size: does each moment say one thing, is every word readable, does the eye know
where to go, does the end card hold for at least 1.5 s? Fix the three weakest moments, render again.
