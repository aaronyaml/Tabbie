<p align="center">
  <img src="docs/images/tabbie.png" alt="Tabbie, a blue mascot with a pink-to-orange swoosh" width="160">
</p>

<h1 align="center">Tabbie</h1>

<p align="center">
  <b>Your coding agents, one inbox.</b> Type a request on your Mac or phone; Tabbie sends it to the
  right agent, in the right repos, and remembers what worked.
</p>

<p align="center">
  <img src="docs/images/project-home.png" alt="A project's home: linked folders, sessions, items to review, and New task / Run Dreamer buttons" width="900">
</p>

---

A personal command centre for coding agents on your Mac. You type a request in a web app (desktop
or phone) and Tabbie routes it:

- **Small, clear jobs** go straight to **Claude Code**, running in the right project folder.
- **Big or ambiguous jobs** go to a **planner** first (Codex, "Astra"). It asks the questions that
  matter, you approve the plan, and Claude Code carries it out.
- **Chat** stays with the hub model.

Use **Claude Code only, Codex only, or both**. `bin/setup` asks which, and you can switch any time in
the WebUI (see [Choosing Claude Code, Codex or both](#choosing-claude-code-codex-or-both)).

On top of that:

- **Projects** group several repos (site + app + API), each with its own instructions, memory and sessions.
- **Memory** is versioned and scoped, and agents only ever *propose* changes to it.
- **The Dreamer** reads past sessions and suggests lessons, backed by evidence, for you to approve.
- **Live agent cards** show what every worker and subagent is doing.
- **Attachments** are passed byte-exact.
- **Usage limits** are shown for every subscription.
- **Takeover**: when Claude hits its limit, the planner can continue the job.

Tabbie is built on two open-source projects and runs entirely on your machine:

- [Hermes Agent](https://github.com/NousResearch/hermes-agent): the hub agent, plugins and CLI.
- [Hermes WebUI](https://github.com/nesquena/hermes-webui): the web app. Tabbie adds an extension to it.

**No API keys.** Every provider signs in with its own subscription login (OAuth). Workers strip
`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` from their environment and refuse to fall back to
pay-per-token billing.

---

## How it works

```mermaid
flowchart LR
    you(["You<br/>WebUI · phone · CLI"]) --> hub["Hermes hub<br/><i>command-centre plugin</i>"]
    hub -->|"chat"| reply(["Hub answers"])
    hub -->|"clear, scoped job"| claude["Claude Code<br/>in the project's folders"]
    hub -->|"big or ambiguous"| plan["Planner (Codex)"]
    plan -->|"questions / plan"| ok{"You approve?"}
    ok -->|"yes"| claude
    ok -->|"revise"| plan
    claude --> trace[("Sanitised trace")]
    trace --> dream["Dreamer"]
    dream -->|"proposals + evidence"| review{"You review"}
    review -->|"approve"| mem[("Versioned memory")]
    mem -.->|"short context"| claude
```

1. Every message passes through the `command-centre` plugin. It scores the request
   (deterministic regex signals, no LLM call), creates a request record on disk, ingests
   attachments and tells the hub which worker to call.
2. Workers read your **verbatim** prompt and the original files from disk, never a paraphrase.
3. After each request a sanitised trace is saved (secrets scrubbed). The Dreamer learns from these
   traces, and nothing becomes memory until you approve it.

---

## Requirements

- **macOS** (Apple silicon or Intel). The core runs anywhere Python does; the always-on services
  use launchd and Bonjour.
- **Python 3.11+**, **git**, **[uv](https://docs.astral.sh/uv/)**
- **[Hermes Agent](https://github.com/NousResearch/hermes-agent)**: provides `hermes`
- **At least one coding agent** (you pick at setup):
  - **[Claude Code](https://docs.anthropic.com/en/docs/claude-code)** with a Claude subscription: provides `claude`;
  - **[Codex CLI](https://github.com/openai/codex)** with a ChatGPT subscription: provides `codex`.
- **Optional:**
  - [Caddy](https://caddyserver.com) (`brew install caddy`), for `https://hermes.local` on your network.

---

## Install

Open the **Terminal** app and paste:

```bash
git clone https://github.com/aaronyaml/Tabbie.git ~/hermes   # the path ~/hermes is required for now
cd ~/hermes
bin/setup
```

`bin/setup` is a guided setup screen. It goes through every step in order, explains each one, and
asks before installing or changing anything. The first run takes about 10–15 minutes:

| Step | What happens |
|---|---|
| 1. About you | Your first name, for the greeting on the chat home ("Morning, Sam", "Afternoon, Sam", "Evening, Sam") |
| 2. Coding agents | **Claude only**, **OpenAI only**, or **Claude and OpenAI** ([what each means](#choosing-claude-code-codex-or-both)) |
| 3. Tools | Checks Homebrew, git, Python, Hermes Agent, Claude Code and/or Codex, and Caddy. Offers to install what's missing |
| 4. Sign in | Opens each provider's own login in your browser (hub model, Claude Code, Codex). No API keys |
| 5. Local files | Folders, settings, the WebUI, the private memory store, plugins, and the hub's worker timeout (your Hermes config is backed up first) |
| 6. WebUI password | Asks for a password (hidden as you type) and saves it where only you can read it |
| 7. Background services | Installs and starts the services so Tabbie runs whenever your Mac is on |
| 8. Secure address | Sets up `https://hermes.local` and offers to trust its certificate on this Mac |
| 9. Your phone | Home Wi-Fi instructions, and optionally Tailscale for anywhere ([details](#use-tabbie-on-your-phone-anywhere-tailscale)) |
| 10. Check | Runs `bin/hub health` and shows the address to open |

It's safe to run again any time: finished steps are ticked and skipped, and anything marked `!`
tells you exactly what to do. When services are already running it asks before restarting them.

Skip the questions with flags: `bin/setup --yes` uses defaults and leaves sign-ins, the password and
phone setup for later. Answer some ahead of time with `--agents claude|codex|claude,codex`,
`--name Sam` and `--services`.

Everything it writes on your Mac is git-ignored: `config/agents.yaml`, `config/projects.yaml`,
`webui-extension/local.js`, `hermes-webui/` and its `.env`, `state/`, `general/`, `workspaces/`, and
`memory/` (its own private git repo). It also appends the hub rules to `~/.hermes/SOUL.md` and
symlinks the four plugins into `~/.hermes/plugins/`.

The sections below explain each part in more detail, if you'd rather do things by hand.

### Sign in (once per provider)

| Provider | Needed for | Command |
|---|---|---|
| Hermes hub model | the dispatcher | `hermes setup` (pick any provider Hermes supports; e.g. `hermes auth add openai-codex --type oauth`) |
| Claude Code (if enabled) | executing work | `claude auth login` (must show `claude.ai` in `claude auth status`) |
| Codex (if enabled) | planner, takeover, or executing work in a Codex-only setup | `codex login` |
| Higgsfield (optional) | image/video generation | `higgsfield auth login` |
| ElevenLabs (optional) | voice/audio | add the hosted MCP below, then `hermes mcp login elevenlabs` |

### Hermes config (`~/.hermes/config.yaml`)

Workers can run for a long time, so raise the tool timeout:

```yaml
timeouts:
  tools:
    sequential_call: 3900   # executor.timeout_seconds in config/router.yaml + margin
```

Optional ElevenLabs tools:

```yaml
mcp_servers:
  elevenlabs:
    url: "https://api.us.elevenlabs.io/v1/mcp"
    auth: oauth
```

### WebUI password

Edit `hermes-webui/.env` and set `HERMES_WEBUI_PASSWORD=…`. The file is git-ignored and `chmod 600`.
Keep the WebUI behind this password: it can start agents on your Mac.

### Start the services

```bash
bin/setup --services      # installs + starts the LaunchAgents
bin/hub health            # one line per component, with the fix command
```

| LaunchAgent | What |
|---|---|
| `ai.hermes.gateway` | Hermes gateway (installed by `hermes gateway install`) |
| `ai.hermes.webui` | WebUI on `127.0.0.1:8787` |
| `ai.hermes.caddy` | HTTPS for `hermes.local`; LAN and Tailscale addresses only, everything else is dropped |
| `ai.hermes.mdns` | Bonjour name `hermes.local` → your Mac's LAN IP |
| `ai.hermes.cc-sidecar` | Data sidecar for the WebUI extension on `127.0.0.1:8794` (token-protected) |
| `ai.hermes.dreamer` | Nightly Dreamer run (03:30, low priority) |

The templates live in `services/*.plist.template`. Nothing is exposed to the internet. Without
Caddy, open `http://127.0.0.1:8787` on the Mac itself.

**Trust the local HTTPS certificate** (once per device). It enables passkeys and the installable app:

- Mac: open Keychain Access and trust Caddy's local CA.
- iPhone:
  1. Open `http://hermes.local/hermes-ca.crt` and install the profile.
  2. Go to Settings › General › About › Certificate Trust Settings and turn on full trust for it.

Open the app with `bin/hub` (or go to `https://hermes.local`).

### Use Tabbie on your phone, anywhere (Tailscale)

`https://hermes.local` only works when your phone is on the same Wi-Fi as your Mac. To reach
Tabbie from anywhere (mobile data, work, travelling) use **[Tailscale](https://tailscale.com)**.
It's a free, private network (a "VPN") between your own devices. Your Mac stays invisible to the
rest of the internet: only devices signed in to *your* Tailscale account can reach it.

You'll do this once. It takes about 10 minutes and needs no networking knowledge.

**What you need:** your Mac (with Tabbie running), your phone, and an email account to sign in
with (Google, Apple, Microsoft or GitHub).

#### Step 1: Create a Tailscale account

1. Go to [tailscale.com](https://tailscale.com) and click **Get started**.
2. Sign in with Google, Apple, Microsoft or GitHub. The personal plan is free.

Use this **same account** on every device below.

#### Step 2: Put Tailscale on your Mac

1. Install **Tailscale** from the [Mac App Store](https://apps.apple.com/app/tailscale/id1475387142)
   (or from [tailscale.com/download](https://tailscale.com/download)).
2. Open it. A small Tailscale icon appears in the menu bar at the top of the screen.
3. Click the icon and choose **Log in**. Sign in with the account from step 1.
4. If macOS asks to **allow a VPN configuration** or a **system extension**, click **Allow**.
   It may send you to System Settings › Privacy & Security to approve it.
5. The menu bar icon should now say **Connected**.

#### Step 3: Turn on secure links (once per account)

1. Open the [Tailscale admin console](https://login.tailscale.com/admin/dns) and go to the **DNS** tab.
2. Make sure **MagicDNS** is on.
3. Scroll to **HTTPS Certificates** and click **Enable HTTPS**.

This gives your Mac a private web address with a padlock, such as `https://my-mac.tail1234.ts.net`.

#### Step 4: Share Tabbie on your private network

Open the **Terminal** app on your Mac (press ⌘ Space, type *Terminal*, press Return). Paste this
line and press Return:

```bash
/Applications/Tailscale.app/Contents/MacOS/Tailscale serve --bg 8788
```

> Didn't install Caddy (no `https://hermes.local`)? Use `8787` instead of `8788` at the end.
> Installed Tailscale with Homebrew instead of the app? The command is just `tailscale serve --bg 8788`.

It prints your Tabbie address, ending in `.ts.net`. **Write it down** (or copy it into a note on
your phone). You only do this once: it keeps working after restarts.

> ⚠️ Use `serve`, **never** `funnel`. *Funnel* puts your Mac on the public internet. *Serve* keeps it
> private to your own devices.

#### Step 5: Put Tailscale on your phone

**iPhone / iPad**

1. Install **Tailscale** from the [App Store](https://apps.apple.com/app/tailscale/id1470499037).
2. Open it and sign in with the **same account** as your Mac.
3. When iOS asks **"Tailscale would like to add VPN configurations"**, tap **Allow**, then enter
   your passcode or use Face ID.
4. Flip the switch at the top to **Connected**. A small **VPN** badge appears in the status bar.

**Android**

1. Install **Tailscale** from [Google Play](https://play.google.com/store/apps/details?id=com.tailscale.ipn).
2. Open it and sign in with the **same account** as your Mac.
3. When Android asks to set up a **VPN connection**, tap **OK**.
4. Make sure the switch shows **Connected**. A key icon appears in the status bar.

In the app's device list you should see your Mac.

#### Step 6: Open Tabbie and add it to your home screen

1. On your phone, open the `.ts.net` address from step 4 in **Safari** (iPhone) or **Chrome** (Android).
2. Log in with your WebUI password (the one in `hermes-webui/.env`).
3. Add it to your home screen so it opens like an app:
   - **iPhone:** tap the **Share** button (square with an arrow), then **Add to Home Screen**.
   - **Android:** tap the **⋮** menu, then **Add to Home screen** (or **Install app**).

Done. Tabbie now works from anywhere your phone has internet.

#### Day-to-day tips

- **The Tailscale switch must be on** on your phone. Turn it off to stop all access.
  On iPhone, Tailscale's settings can turn it on automatically when needed (*VPN On Demand*).
- **Your Mac must be on, awake and logged in.** In System Settings › Battery (or Energy Saver) ›
  Options, turn on **Prevent automatic sleeping on power adapter when the display is off**,
  and keep it plugged in. The screen can be off and locked.
- **One VPN at a time on phones.** If you use another VPN app, switch it off while you use Tabbie.
- **Add more devices** (an iPad, a laptop) the same way: install Tailscale, sign in with the same
  account, open the same address.
- **Lost a phone?** Remove it in the [Tailscale admin console](https://login.tailscale.com/admin/machines)
  (⋯ › Remove) and change your WebUI password.

#### If it doesn't load

1. Check that **both** the Mac and the phone show **Connected** in Tailscale.
2. Check that the Mac is awake and Tabbie is running: on the Mac, run `bin/hub health`.
3. On the Mac, run `/Applications/Tailscale.app/Contents/MacOS/Tailscale serve status`. It should
   show your `.ts.net` address, `(tailnet only)`, and `proxy http://127.0.0.1:8788` (or `8787`).
   If it's empty, repeat step 4.
4. A "certificate" error on the first visit usually clears after a minute. HTTPS links are created
   on first use.

To stop sharing Tabbie over Tailscale: `/Applications/Tailscale.app/Contents/MacOS/Tailscale serve reset`.

**Why this is safe:** Tailscale Serve only answers devices signed in to your Tailscale account.
Caddy also refuses anything that isn't a home-network or Tailscale address, and the WebUI still
asks for your password. Nothing is opened on your router, and port 8787 is never exposed to the internet.

---

## Using it

### Page addresses

Every page has its own address, so a refresh (or a bookmark) lands you back on it instead of the chat:

| Address | Page |
|---|---|
| `/` or `/session/<id>` | Chat |
| `/projects` | Projects |
| `/motion`, `/motion/<id>` | Motion studio |
| `/tasks`, `/kanban`, `/todos`, `/skills`, `/memory`, `/workspaces`, `/profiles`, `/insights`, `/logs`, `/settings` | That page |

These go through Caddy (`https://hermes.local` or your Tailscale address). On the bare
`http://127.0.0.1:8787` only the chat addresses work.

### Routing

| Route | When | Flow |
|---|---|---|
| **Hub** | General chat with no project selected | The hub answers directly |
| **Claude** (default) | Clear, scoped work: "fix this error", "run the tests", "make the button black" | Claude Code runs in the project; follow-ups resume the same Claude session (≤ 2 h) |
| **Planner → approval → Claude** | Broad or ambiguous work: features, migrations, architecture, several deliverables | The planner asks material questions or proposes a plan. You approve, it writes a brief, and Claude executes with the brief, your original prompt and the original attachments |

- Message prefixes:
  - `@claude …` runs Claude now.
  - `@astra …` / `@plan …` goes through the planner.
  - `@project:<name> …` picks the project.
- Approval replies:
  - `yes` / `go` approves;
  - `yes but …` approves with changes;
  - anything else revises;
  - `cancel` drops the request.
- Execution of a planned request is refused in code unless it was approved.
- Every reply starts with `Route: …`. The full decision log is at `bin/hub logs decisions`.

### Projects

A **Project** groups the folders or repos that make up one product. Nothing becomes a project
automatically. In the WebUI, open the rail → **Projects** → **New project**:

1. Name it and add folders. Pick from suggested repos, browse, or type a path, and select several at once.
2. Choose the primary folder; workers start there and get the others as extra directories.
3. **New task** opens a session that knows the project. Workers get a short context block, never memory dumps.

The project home has tabs for:

- folders, sessions and memory (search it, or delete entries);
- the Dreamer (findings with evidence; approve or reject proposals with a diff);
- skills, instructions, architecture and settings.

<p align="center">
  <img src="docs/images/phone.png" alt="On a phone: a project home, the multi-select folder picker, and a Dreamer proposal with its diff" width="900">
</p>

Folder access is limited to linked folders under your home folder. It never includes hidden folders
(`~/.ssh`…), personal folders (Documents, Desktop…) or Tabbie's own state.

For lighter routing, `config/projects.yaml` names folders the router can target, and
`/cc workspace create <name> <folders…>` makes a multi-folder workspace.

### Memory and the Dreamer

- `memory/` is a separate git repo:
  - Markdown memories with provenance, scoped `global/`, `projects/<id>/` or `agents/<a>/`;
  - every write is locked, hash-checked and committed.
- **Only you change memory.**
  - Workers can't edit it.
  - They file proposals (`hmem propose …`), which you approve in the WebUI or with `hmem approve <id>`.
  - Content from web pages or tools stays marked `external` and is never injected into a prompt as fact.
- **The Dreamer** (`hmem dream`, nightly, or after N sessions) looks for repeated failures and the
  rules you stated across several conversations. It files a few proposals, each with evidence. In a
  project it only reads that project's sessions.
- CLI reference: `hmem search|read|proposals|show|approve|reject|history|rollback|stats|doctor`, and
  `hmem project …`.

<p align="center">
  <img src="docs/images/dreamer.png" alt="The Dreamer tab: last dream, sessions analysed, pending proposals and findings with evidence" width="900">
</p>

Design notes: `docs/memory/ARCHITECTURE.md`, `docs/projects/ARCHITECTURE.md`.

### Attachments, agents, questions, takeover

- **Attachments** (uploads, `@file:` refs, pasted paths):
  - stored byte-exact and read-only under `state/requests/<id>/attachments/`;
  - a manifest records each file's name, type, size and sha256;
  - derived files (extracted ZIPs, PDF text, media info) sit beside the originals.
- **Agents panel**: each reply shows a live block of the worker and its subagents (status, latest
  step, tokens, time). The Agents pill opens the whole session.
- **Questions**: when a worker needs a decision it asks a structured question card, and your reply
  resumes the same session.
- **Takeover**: if Claude hits its usage limit, Tabbie offers to let the planner continue from where
  it stopped. `/takeover` toggles it.
- **Limits**: `/limit` or `bin/hub limits` shows % used and reset times for each signed-in subscription.

### Choosing Claude Code, Codex or both

| Setup | Who executes | Who plans big jobs | Usage limit hit |
|---|---|---|---|
| **Both** (default) | Claude Code, or Codex if you pick an OpenAI model for the chat | Codex ("Astra") | Codex can take over (`/takeover`) |
| **Claude Code only** | Claude Code | Claude Code (`claude-cli` planner) | Tabbie tells you; no takeover |
| **Codex only** | Codex | Codex | no fallback agent |

Change it any time from the composer's **model picker → Agents** (Claude Code / Codex toggles; at
least one stays on). It applies on the next request with no restart. It's stored in
`config/agents.yaml`:

```yaml
enabled: [claude, codex]   # or [claude], or [codex]
```

With one agent off, its models disappear from the picker, the planner switches to the agent you
have, `/limit` and `bin/hub health` skip it, and chat titles and Dreamer phrasing (which use Claude
Haiku) fall back to plain text when Claude Code is off. No file means both (older installs).

### Optional: motion studio

`/motion <brief>` runs Claude in `~/motion` (a Remotion project of your own) with a `motion` skill.
The motion panel in the WebUI expects that folder. Ignore this if you don't use it.

---

## Configuration

| File | What | Tracked? |
|---|---|---|
| `config/router.yaml` | Router, planner and executor settings, memory and Dreamer options. Re-read on every request | yes (defaults) |
| `config/agents.yaml` | Which coding agents to use: Claude Code, Codex or both | no (written by `bin/setup` / the WebUI) |
| `config/projects.yaml` | Named folders and workspaces for routing | no (from `.example`) |
| `hermes-webui/.env` | WebUI host, port and password | no (from `config/webui.env.example`) |
| `webui-extension/local.js` | Your home path and name for the extension | no (from `local.example.js`) |
| `~/.hermes/config.yaml`, `~/.hermes/SOUL.md` | Hermes Agent's own config and hub rules | outside the repo |

To change the planner, edit `config/router.yaml`; it applies on the next request.

```yaml
planner:
  provider: codex-cli      # or claude-cli (used automatically when Codex is off)
  model: <a model your login exposes>   # list them with: /cc planner
```

If the model isn't available to your login, planning fails with a clear error and never swaps in
another model. Auto-approval of Dreamer proposals is off (`memory.auto_approve`); it never covers
global memory, skills or deletions.

---

## Security model

- **No secrets in the repo.** `.gitignore` excludes `.env*`, token, credential and key files,
  `memory/`, `state/` and all per-machine config. Provider logins live in each tool's own store
  (Keychain, `~/.claude`, `~/.codex`, `~/.hermes`).
- **Traces are scrubbed.** Keys, tokens, passwords and URL credentials are removed before anything
  is written to disk.
- **The WebUI is the only door.** It is password-protected and listens on localhost; Caddy exposes
  it to your LAN and Tailscale only. The extension's sidecar needs a token minted by the WebUI
  after a one-time consent.
- **Workers can't rewrite memory or approve anything.** Their file tools are denied on `memory/`
  and the sidecar token. User-only `hmem` commands refuse to run inside a worker. Memory files
  edited outside the store are ignored until you review them (`hmem doctor`).
- **Untrusted text stays data.** Repo files, web pages and tool output can't become standing
  instructions: they are scanned for injection, kept `external`, and must be approved by you.

Defence in depth, not a sandbox: a worker runs as your user with Claude Code's permission mode.
Use Claude Code's own permission settings for hard limits.

---

## Repo layout

| Path | What |
|---|---|
| `plugin/command-centre/` | Router plugin: `router.py` scoring, `attachments.py`, `state.py` request lifecycle, `providers.py` Claude/planner runners and auth checks, `projects.py`, `agents.py`, `questions.py`, `sidecar.py` |
| `plugin/fragment-memory/` | Hub memory tools (`mem_save/search/read/forget`) |
| `plugin/limits/` | `/limit` usage view |
| `plugin/workboard/` | Kanban cards and todos shared with the WebUI |
| `hmem/`, `bin/hmem` | Memory store, tracer, Dreamer, Projects registry, CLI, HTTP API |
| `webui-extension/` | WebUI extension: Apple-style skin, Projects, agents, questions, limits, workboard, motion studio |
| `brand/` | Icons, mascot animation frames, login skin |
| `services/` | LaunchAgent templates and the Caddyfile |
| `bin/` | `setup`, `hub` (open, cli, health, restart, logs, import, limits), service entrypoints |
| `config/` | Router defaults and the `*.example` files |
| `docs/` | Architecture notes |
| `tests/` | Test suite |

## Tests

```bash
uv run -q --no-project --with pytest --with ruamel.yaml python -m pytest tests -q
```

The tests use temporary folders only and never touch your memory or state.

## Troubleshooting

- `bin/hub health` checks every component and prints the fix command.
- `bin/hub logs webui|gateway|caddy|mdns|decisions` shows logs.
- **Page hangs in a Mac browser**: System Settings › Privacy & Security › Local Network → allow the browser.
- **Wrong route**: `/cc route <text>` shows the score. Tune the signals in `router.py` or `router.plan_threshold`.
- **Claude refuses to run**: `claude auth status` must show a `claude.ai` login. API keys are refused on purpose.
- **Planner errors**: `/cc planner` lists the models your login exposes.
- **Restart everything**: `bin/hub restart`. Don't restart while a request is running.

## License

MIT. See `LICENSE`.
