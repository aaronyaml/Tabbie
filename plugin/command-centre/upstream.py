"""Upstream versions (Hermes WebUI and Hermes Agent) versus what Tabbie was tested with.

config/upstream.lock names the commits Tabbie was tested with. Tabbie reads some of their internals
directly (state.db tables, a few Agent modules, plugin hooks, the WebUI's file layout), so a different
upstream version can break Team or Insights without an error. contract() checks those parts are there;
status() compares installed commits with the lock. Standard library only (the updater uses it).
"""
from __future__ import annotations

import os
import re
import sqlite3
import subprocess
from pathlib import Path

from .common import ROOT

LOCK = Path(__file__).resolve().parents[2] / "config" / "upstream.lock"  # this checkout's lock
_SHA = re.compile(r"^[0-9a-f]{40}$")


def _agent_dir() -> Path:
    return Path(os.environ.get("HERMES_AGENT_DIR") or Path.home() / ".hermes" / "hermes-agent").expanduser()


def _hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes").expanduser()


def dirs() -> dict:
    return {"webui": ROOT / "hermes-webui", "agent": _agent_dir()}


def lock() -> dict:
    out = {}
    try:
        for line in LOCK.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\s*(webui|agent)\s*=\s*([0-9a-f]{40})\s*$", line)
            if m:
                out[m.group(1)] = m.group(2)
    except OSError:
        pass
    return out


def _git(d: Path, *args: str, timeout: int = 20) -> tuple[int, str]:
    try:
        r = subprocess.run(["git", "-C", str(d), *args], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return r.returncode, (r.stdout or r.stderr).strip()


def relation(d: Path, want: str) -> str:
    """'same', 'behind' (want is newer), 'ahead' (installed is newer), 'other', 'unknown' or 'missing'."""
    if not (d / ".git").exists():
        return "missing"
    rc, head = _git(d, "rev-parse", "HEAD")
    if rc or not _SHA.match(head):
        return "unknown"
    if head == want:
        return "same"
    if _git(d, "cat-file", "-e", f"{want}^{{commit}}")[0]:
        return "unknown"  # the locked commit hasn't been fetched here
    if _git(d, "merge-base", "--is-ancestor", head, want)[0] == 0:
        return "behind"
    if _git(d, "merge-base", "--is-ancestor", want, head)[0] == 0:
        return "ahead"
    return "other"


# Parts of upstream Tabbie relies on: (what, file under the Agent checkout, text that must be in it)
AGENT_NEEDS = [
    ("live progress in chats", "agent/tool_progress_relay.py", "def current_tool_progress"),
    ("long hub tools", "tools/registry.py", "execution_timeout"),
    ("plugin hooks", "hermes_cli/plugins.py", "hold_stream"),
    ("plugin hooks", "hermes_cli/plugins.py", "def register_hook"),
    ("Workboard", "hermes_cli/kanban_db.py", ""),
]
DB_NEEDS = {  # Insights reads these from ~/.hermes/state.db
    "sessions": {"id", "source", "title", "cwd", "started_at"},
    "messages": {"session_id", "timestamp", "role"},
    "session_model_usage": {"session_id", "model", "input_tokens", "output_tokens", "cache_read_tokens",
                            "cache_write_tokens", "api_call_count", "last_seen"},
}
WEBUI_NEEDS = [("extensions", "api/extensions.py"), ("chat sessions", "api/routes.py"), ("startup", "bootstrap.py")]


def contract() -> list[str]:
    """Problems found (empty when everything Tabbie relies on is there)."""
    probs = []
    a = _agent_dir()
    if a.exists():
        for what, rel, text in AGENT_NEEDS:
            p = a / rel
            try:
                if not p.exists() or (text and text not in p.read_text(encoding="utf-8", errors="replace")):
                    probs.append(f"Hermes Agent: {what} ({rel}) changed")
            except OSError:
                probs.append(f"Hermes Agent: couldn't read {rel}")
    db = _hermes_home() / "state.db"
    if db.exists():
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
            try:
                tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                for t, cols in DB_NEEDS.items():
                    if t not in tables:
                        probs.append(f"Hermes Agent: state.db has no {t} table (Insights)")
                        continue
                    have = {r[1] for r in conn.execute(f"PRAGMA table_info({t})")}
                    if cols - have:
                        probs.append(f"Hermes Agent: state.db {t} lacks {', '.join(sorted(cols - have))} (Insights)")
            finally:
                conn.close()
        except sqlite3.Error:
            pass  # busy or locked right now: not evidence of a changed format
    w = dirs()["webui"]
    if w.exists():
        for what, rel in WEBUI_NEEDS:
            if not (w / rel).exists():
                probs.append(f"WebUI: {what} ({rel}) moved")
    return probs


def patches() -> list[str]:
    """WebUI patches that aren't applied (bin/webui-patches status)."""
    tool = ROOT / "bin" / "webui-patches"
    if os.name == "nt" or not tool.exists() or not (dirs()["webui"] / ".git").exists():
        return []
    try:
        r = subprocess.run([str(tool), "status"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [line.split(None, 1)[1] for line in r.stdout.splitlines() if line.startswith(("missing", "CONFLICT"))]


def status() -> dict:
    lk = lock()
    out = {"ok": True, "problems": contract(), "patches_missing": patches(), "versions": {}}
    for name, d in dirs().items():
        want = lk.get(name, "")
        rel = relation(d, want) if want else "unknown"
        rc, head = _git(d, "rev-parse", "HEAD")
        out["versions"][name] = {"installed": head[:10] if not rc else "", "tested": want[:10], "relation": rel}
    out["ok"] = not out["problems"]
    return out
