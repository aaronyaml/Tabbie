"""Do the installed Claude Code and Codex still have every option Tabbie passes them?

They update themselves, and an old copy (or a renamed option) makes every request fail with a bare usage
error. This reads each tool's --help once per installed version and says what's missing, in plain words.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

try:
    from .common import STATE_DIR
except ImportError:  # run as a script: bin/hub doctor
    STATE_DIR = Path(__file__).resolve().parents[2] / "state"

# Every option Tabbie's command lines use (providers.py, prep.py, hmem/dreamer.py, team/mailroom.py).
NEEDS = {
    "claude": (["--help"], ["--output-format", "--input-format", "--include-partial-messages", "--verbose", "--permission-mode",
                            "--append-system-prompt", "--setting-sources", "--settings", "--strict-mcp-config", "--mcp-config",
                            "--exclude-dynamic-system-prompt-sections", "--plugin-dir", "--disallowedTools", "--add-dir",
                            "--model", "--effort", "--resume", "--no-session-persistence", "--tools", "--json-schema"]),
    "codex": (["exec", "--help"], ["--skip-git-repo-check", "--json", "--ephemeral", "--add-dir", "--output-last-message",
                                   "--sandbox", "--cd", "--model", "--config"]),
}
OPTIONAL = {"claude": ["--safe-mode"]}  # nice to have (Team mail runs without customisations); not a blocker
NAMES = {"claude": "Claude Code", "codex": "Codex"}
UPDATE = {"claude": "claude update", "codex": "npm install -g @openai/codex (or brew upgrade codex)"}
CACHE = STATE_DIR / "cli-flags.json"


def _stamp(path: str) -> str:
    try:
        st = Path(path).resolve().stat()
        return f"{Path(path).resolve()}:{int(st.st_mtime)}:{st.st_size}"
    except OSError:
        return path


def missing(tool: str, *, use_cache: bool = True) -> list[str] | None:
    """Options Tabbie needs that this tool's --help doesn't list; None if the tool isn't installed."""
    exe = shutil.which(tool)
    if not exe:
        return None
    try:
        cache = json.loads(CACHE.read_text(encoding="utf-8")) if use_cache else {}
    except (OSError, ValueError):
        cache = {}
    stamp = _stamp(exe)
    hit = cache.get(tool)
    if hit and hit.get("stamp") == stamp:
        return hit["missing"]
    args, need = NEEDS[tool]
    try:
        r = subprocess.run([exe, *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
                           stdin=subprocess.DEVNULL, env={**os.environ, "NO_COLOR": "1"})
        text = r.stdout + r.stderr
    except (OSError, subprocess.SubprocessError):
        return []  # couldn't ask: don't cry wolf
    if len(text) < 200:
        return []  # not a real help page (a wrapper, a crash): nothing to compare against
    gone = [f for f in need if f not in text]
    cache[tool] = {"stamp": stamp, "missing": gone}
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(cache), encoding="utf-8")
    except OSError:
        pass
    return gone


def problems(tools=("claude", "codex")) -> list[str]:
    """Plain-language problems, one per tool that's installed but too old (or changed)."""
    out = []
    for t in tools:
        gone = missing(t)
        if gone:
            out.append(f"{NAMES[t]} is missing options Tabbie uses ({', '.join(gone[:4])}{'…' if len(gone) > 4 else ''}). "
                       f"Update it: {UPDATE[t]}")
    return out


if __name__ == "__main__":  # bin/hub doctor
    import sys
    found = False
    for t in NEEDS:
        gone = missing(t, use_cache=False)
        if gone is None:
            continue
        found = True
        print(("  \033[32m✓\033[0m " + NAMES[t] + " has every option Tabbie uses") if not gone else
              ("  \033[31m✗\033[0m " + problems((t,))[0]))
    if not found:
        print("  \033[31m✗\033[0m Neither Claude Code nor Codex is installed")
    sys.exit(1 if not found or problems() else 0)
