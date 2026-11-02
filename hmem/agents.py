"""Which coding agents Tabbie uses on this computer: Claude Code, Codex, or both.

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
        text = path().read_text(encoding="utf-8")
    except OSError:
        return list(AGENTS)
    m = re.search(r"^enabled:\s*\[([^\]]*)\]", text, re.M)
    got = [a for a in AGENTS if m and a in {x.strip().strip("'\"").lower() for x in m.group(1).split(",")}]
    return got or list(AGENTS)  # an empty or broken file never leaves Tabbie with no worker


def has(agent: str) -> bool:
    return agent in enabled()


def dynamic_enabled() -> bool:
    """Dynamic agents (Claude Code and Codex may hand parts of a job to each other). Default ON: only an
    explicit `dynamic: false` turns it off."""
    try:
        text = path().read_text(encoding="utf-8")
    except OSError:
        return True
    m = re.search(r"^dynamic:\s*(\S+)", text, re.M)
    return not (m and m.group(1).strip().strip("'\"").lower() in ("false", "no", "off", "0"))


def takeover_enabled() -> bool:
    """Takeover: a job that hits Claude's or Codex's usage limit carries on in the other one (same task,
    no question). Default ON: only an explicit `takeover: false` turns it off."""
    try:
        text = path().read_text(encoding="utf-8")
    except OSError:
        return True
    m = re.search(r"^takeover:\s*(\S+)", text, re.M)
    return not (m and m.group(1).strip().strip("'\"").lower() in ("false", "no", "off", "0"))


def dynamic_allowed(linked: dict | None = None) -> bool:
    """The setting is on, both agents are enabled, and (when given) both are signed in."""
    on = enabled()
    both = "claude" in on and "codex" in on
    return dynamic_enabled() and both and (linked is None or all(linked.get(a) for a in AGENTS))


def save(agents: list[str] | None = None, dynamic: bool | None = None, takeover: bool | None = None) -> list[str]:
    """Write config/agents.yaml. None for any argument keeps its current setting."""
    if agents is None:
        picked = enabled()
    else:
        picked = [a for a in AGENTS if a in {str(x).strip().lower() for x in agents or []}]
    if not picked:
        raise ValueError("pick at least one agent: claude, codex or both")
    dyn = dynamic_enabled() if dynamic is None else bool(dynamic)
    take = takeover_enabled() if takeover is None else bool(takeover)
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text("# Which coding agents Tabbie uses on this computer: claude, codex, or both.\n"
                   "# Change it in the WebUI (model picker → Agents) or with bin/setup. No restart needed.\n"
                   f"enabled: [{', '.join(picked)}]\n"
                   "# Hand-offs: with both agents signed in, Claude Code and Codex may pass part of a job to each other.\n"
                   f"dynamic: {'true' if dyn else 'false'}\n"
                   "# Takeover: a job that hits one agent's usage limit carries on in the other (same task).\n"
                   f"takeover: {'true' if take else 'false'}\n", encoding="utf-8")
    os.replace(tmp, p)
    return picked
