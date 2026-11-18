# Hermes — map

Hub that routes requests to Claude Code / Astra. Code is the source of truth; this file only points.

| Need | Look at |
|---|---|
| How it works | `README.md` |
| Router, request lifecycle, workers | `plugin/command-centre/` (`router.py`, `state.py`, `providers.py`) |
| Settings (re-read per request) | `config/router.yaml`, `config/projects.yaml` |
| WebUI additions | `webui-extension/` (bump versions in `extensions.json`) |
| Memory system | `hmem/` code, `memory/README.md` map, `docs/memory/ARCHITECTURE.md` design |
| Memory CLI | `bin/hmem search|read|propose|proposals|approve|dream|stats|doctor` |
| Services | `services/*.plist` (LaunchAgents) |
| Tests | `uv run -q --no-project --with pytest --with ruamel.yaml --with cryptography==48.0.1 --with websockets==17.4 python -m pytest tests -q` |
| UI testing (browser, reload, iPhone) | `docs/testing/UI_TESTING.md`, helpers `docs/testing/ui_helpers.py` |

Rules:
- Never restart the WebUI or gateway while requests or hub turns are running.
- Back up config before changing it (`backups/`). No API-key billing. No secrets in logs, git or memory.
- Don't edit `memory/` by hand; use `bin/hmem propose`. Most tasks propose nothing.
- Any new feature or UI change: run the full UI test process (`docs/testing/UI_TESTING.md`: real browser, reload tests, iPhone 15 Pro Max) and fix what it finds BEFORE committing or pushing.
- Commits: NEVER add `Co-Authored-By` or `Claude-Session` lines (or any co-author/attribution trailer). Plain messages only.

Secrets (1Password CLI, vault "Personal"):
- NEVER run `op read`, `op item get`, `op inject` to stdout, or print environment variables (`echo $VAR`, `env`, `printenv`, `set`, `export -p`). Never read `.env` files.
- ALWAYS run anything that needs a secret as `bin/with-secrets -- <command>` (Tabbie vault, via the service account and 1Password's SDK; `--list` for names): values go into that process's memory only, never into output, files, logs, git or memory. Check `--list` before asking the user for a key. Don't call the `op` CLI from Tabbie work (it triggers macOS/1Password permission prompts nobody away from the Mac can answer); `op run --env-file=".env.tpl"` only when the user is at the Mac and asks. Never echo a secret even inside with-secrets (it masks, but don't).
- New secret: add a reference line (`NAME="op://Personal/<item>/<field>"`) to `.env.tpl`; the user stores the value in 1Password. `op whoami` / `op vault list` are fine (no secret values).
