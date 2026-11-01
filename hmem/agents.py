"""Which coding agents Tabbie uses on this Mac: Claude Code, Codex, or both.

One small file, config/agents.yaml (per machine, git-ignored), written by `bin/setup` and by the
WebUI's model picker (Settings). Everything that would start a `claude` or `codex` process asks here
first, so a Codex-only or Claude-only install never tries the other CLI. Stdlib only.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

AGENTS = ("claude", "codex")
LABEL = {"claude": "Claude Code", "codex": "Codex"}


def path() -> Path:
    home = Path(os.environ.get("COMMAND_CENTRE_HOME", Path.home() / "hermes")).expanduser()
    return home / "config" / "agents.yaml"


def enabled() -> list[str]:
    """Enabled agents in a fixed order. No file (older installs) = both."""
    try:
        text = path().read_text()
    except OSError:
        return list(AGENTS)
    m = re.search(r"^enabled:\s*\[([^\]]*)\]", text, re.M)
    got = [a for a in AGENTS if m and a in {x.strip().strip("'\"").lower() for x in m.group(1).split(",")}]
    return got or list(AGENTS)  # an empty or broken file never leaves Tabbie with no worker


def has(agent: str) -> bool:
    return agent in enabled()


def save(agents: list[str]) -> list[str]:
    picked = [a for a in AGENTS if a in {str(x).strip().lower() for x in agents or []}]
    if not picked:
        raise ValueError("pick at least one agent: claude, codex or both")
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text("# Which coding agents Tabbie uses on this Mac: claude, codex, or both.\n"
                   "# Change it in the WebUI (model picker → Agents) or with bin/setup. No restart needed.\n"
                   f"enabled: [{', '.join(picked)}]\n")
    os.replace(tmp, p)
    return picked
