# UI testing (end to end, in a real browser)

How to test Tabbie's whole UI by hand or with an agent: every screen, every common action, what
survives a page reload, desktop and iPhone. Use it after any UI change and before releases.

The loop: **test → log every issue → plan fixes → fix (delegate by file) → re-test → repeat until
nothing fails.** "Issue" means anything that errors, looks wrong, says something unclear, is
missing, or doesn't behave as a user would expect, not just crashes.

## 1. Set up

**Browser.** Use [browser-harness](https://github.com/browser-use/browser-harness) against a
dedicated automation Chrome with its own profile (never the everyday one):

```bash
open -na "Google Chrome" --args --remote-debugging-port=9222 \
  --user-data-dir="$HOME/.browser-harness-chrome" --no-first-run --no-default-browser-check
export BU_CDP_URL=http://127.0.0.1:9222
```

**Address.** Test at `http://127.0.0.1:8788`: Caddy's local port, so it's the full Tabbie (icons,
`/kanban`, `/projects`, `/motion`), and it counts as a local address. Not `:8787` (bare upstream WebUI).

**Sign in.** The WebUI password is the user's. The user signs in once in that Chrome window (the
profile remembers it), or gives it for a local test session. Never write it into files, logs or
screenshots, and never sign in on `hermes.local` or a Tailscale address on the user's behalf.

**Run today's code.** Before testing, bump changed files in `webui-extension/extensions.json`, and if
plugin/ or hmem/ changed, restart the sidecar and WebUI, only when nothing is running:

```bash
pgrep -f "claude -p|codex exec|remotion render"            # must print nothing
curl -s http://127.0.0.1:8787/health                       # active_streams and active_runs must be 0
launchctl kickstart -k gui/$(id -u)/ai.hermes.cc-sidecar
launchctl kickstart -k gui/$(id -u)/ai.hermes.webui
```

**Helpers.** `docs/testing/ui_helpers.py` (load with `exec(open(...).read())` inside a harness call):
`go(url)`, `reload()`, `shot(name)`, `rail("Kanban")`, `errs()` (JS errors, console errors, failed
requests since the last call), `set_value(sel, text)`, `chip()`, `queue_count()`, `iphone()`,
`pshot(name)`, `reload_css(name)`. Screenshots land in `state/uitest/shots/` (git-ignored).

Harness gotchas: its `fill_input`/`type_text` + `press_key("Enter")` can double characters or add a
stray newline. Use `set_value()` for fields. Read click coordinates from an uncropped screenshot.

## 2. What to cover

Collect `errs()` after every step and screenshot every screen. Look at each screenshot properly:
overlaps, clipping, truncation, jargon, wrong state, missing feedback.

| Area | Do |
|---|---|
| Home | greeting, suggestion cards, "Jump back in" titles, update banner(s) |
| Rail | open every panel: Chat, Motion, Projects, Scheduled jobs, Kanban, Skills, Memory, Spaces, Profiles, Todos, Insights, Logs, Settings |
| Composer | model picker (provider → model → effort, Agents toggle), workspace chip, profile, slash menu (`/limit` with one Enter), focus glow |
| Chat run | send a request that routes to Claude Code (e.g. "run sleep 45, then reply X"): live worker card, route badge, final reply |
| Queue | while it runs: queue a follow-up (goes to Claude in seconds, "Sent to Claude" note, reply answers both), keep a half-typed draft (must survive), Send now |
| /limit | skeleton first, then connected accounts only |
| Projects | open a project, every tab, counts and copy |
| Motion | new film, each step's controls, delete film (no leftover "Session not available") |
| Settings | System (Tabbie row, updates), Extensions (helper health), Appearance |
| Controls | every dropdown opens the custom list; checkboxes are custom; keyboard works |

## 3. Reload tests (do an action → reload → check)

For each, note whether it **persists, changes or gets dropped**:

- model + effort for a chat; composer draft; workspace chip
- chat rename; theme; Kanban task added
- open project + tab (address `/projects#<id>/<tab>`)
- Motion film: step, audio choice, typed prompt, a job started just before reload
- **send + queue + reload mid-run**: the queued message must come back (card shown) and still be
  sent or pushed into the worker; nothing silently dropped

## 4. iPhone 15 Pro Max (home-screen app)

`iphone()` sets 430×932 @3×, the iOS UA and safe-area insets (59 top for the Dynamic Island, 34
bottom for the home bar), and makes the page behave as an installed app (`navigator.standalone`
plus the `display-mode: standalone` CSS, which Chrome can't emulate natively). `pshot()` draws the
island, clock and home bar over the screenshot so you can see what they'd cover. Check every screen
from section 2, the drawer (open via Menu), sheets and dropdowns (bottom sheet on phones), tap
targets, and that nothing sits under the island or the home bar.

Use a fresh tab for desktop runs afterwards (the standalone shim stays in the tab).

## 5. Log, fix, re-test

- Log each issue in `state/uitest/ISSUES.md` (git-ignored): number, severity (High/Med/Low), area,
  what happened vs what was expected, how to reproduce, owner.
- Plan fixes, then split them by file so parallel agents never edit the same file. Give each agent
  the issue text, the files it owns, "don't edit hermes-webui/ or extensions.json, don't restart or
  commit", and how to verify (node --check, pytest, a stubbed simulation).
- Bump versions, run `uv run -q --no-project --with pytest --with ruamel.yaml python -m pytest tests -q`,
  restart (idle check first), and repeat sections 2-4 for every fixed issue. Anything still failing
  goes back to step 5.
- Clean up test data afterwards (test films, Kanban tasks, renamed chats).
