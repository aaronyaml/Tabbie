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
| Tests | `uv run -q --no-project --with pytest --with ruamel.yaml python -m pytest tests -q` |
| UI testing (browser, reload, iPhone) | `docs/testing/UI_TESTING.md`, helpers `docs/testing/ui_helpers.py` |

Rules:
- Never restart the WebUI or gateway while requests or hub turns are running.
- Back up config before changing it (`backups/`). No API-key billing. No secrets in logs, git or memory.
- Don't edit `memory/` by hand; use `bin/hmem propose`. Most tasks propose nothing.
- Any new feature or UI change: run the full UI test process (`docs/testing/UI_TESTING.md`: real browser, reload tests, iPhone 15 Pro Max) and fix what it finds BEFORE committing or pushing.
- Commits: NEVER add `Co-Authored-By` or `Claude-Session` lines (or any co-author/attribution trailer). Plain messages only.
