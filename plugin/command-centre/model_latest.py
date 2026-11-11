"""The newest model in each family, so Tabbie never offers or runs an outdated one.

Claude Code resolves family aliases (opus, fable, sonnet, haiku) to the newest model on the user's plan;
one tiny `claude -p --model <alias>` run reports which concrete model that is. Codex keeps the server's
model list in ~/.codex/models_cache.json (gpt-<version>-<family>). Both are read at most every 12 hours,
in the background, and cached in state/model-latest.json. Nothing here uses an API key.

    families()          {"anthropic": {"fable": {"id", "name"}, ...}, "openai": {...}}
    upgrade(model_id)   an older id in a known family -> that family's newest id (unknown ids unchanged)
    refresh_soon()      start a background refresh if the cache is stale (cheap to call often)
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from .procutil import procutil

HOME = Path(__file__).resolve().parents[2]
CACHE = HOME / "state" / "model-latest.json"
LOCK = HOME / "state" / "locks" / "model-latest.lock"
CODEX_CACHE = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "models_cache.json"
MAX_AGE = 12 * 3600
ANTHROPIC_FAMILIES = ("opus", "fable", "sonnet", "haiku")
OPENAI_FAMILIES = ("astra", "sol", "terra", "luna")

_CLAUDE_RX = re.compile(r"^claude-([a-z]+)-(\d+)-(\d+)(?:-\d{8})?$")
_GPT_RX = re.compile(r"^gpt-(\d+(?:\.\d+)?)-([a-z]+)$")
_SCRUB = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL",
          "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL",
          "ANTHROPIC_SMALL_FAST_MODEL", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")

_mem: dict = {"data": None, "mtime": 0.0}
_busy = threading.Lock()


def parse(model_id: str) -> tuple[str, str, tuple[int, ...]] | None:
    """('anthropic'|'openai', family, version) for a model id in a known family shape."""
    m = _CLAUDE_RX.match(model_id or "")
    if m:
        return "anthropic", m.group(1), (int(m.group(2)), int(m.group(3)))
    m = _GPT_RX.match(model_id or "")
    if m:
        return "openai", m.group(2), tuple(int(x) for x in m.group(1).split("."))
    return None


def display_name(model_id: str) -> str:
    p = parse(model_id)
    if not p:
        return model_id
    prov, fam, ver = p
    v = ".".join(str(x) for x in ver)
    return f"{fam.capitalize()} {v}" if prov == "anthropic" else f"GPT-{v} {fam.capitalize()}"


def _read_cache() -> dict:
    try:
        st = CACHE.stat()
    except OSError:
        return {}
    if _mem["data"] is not None and _mem["mtime"] == st.st_mtime:
        return _mem["data"]
    try:
        data = json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    _mem.update(data=data, mtime=st.st_mtime)
    return data


def _codex_latest() -> dict:
    """Newest listed Codex model per family, from Codex's own copy of the server's model list."""
    try:
        models = json.loads(CODEX_CACHE.read_text(encoding="utf-8")).get("models") or []
    except (OSError, ValueError, AttributeError):
        return {}
    best: dict[str, tuple] = {}
    for m in models:
        slug = m.get("slug") or ""
        p = parse(slug)
        if not p or p[0] != "openai" or m.get("visibility") == "hide":
            continue
        fam, ver = p[1], p[2]
        if fam not in best or ver > best[fam][0]:
            best[fam] = (ver, slug)
    return {fam: {"id": slug, "name": display_name(slug)} for fam, (_, slug) in best.items()}


def _claude_resolve(alias: str) -> str | None:
    claude = procutil.local_bin("claude")
    env = {k: v for k, v in os.environ.items() if k not in _SCRUB}
    try:
        with tempfile.TemporaryDirectory() as cwd:
            out = subprocess.run(
                [claude, "-p", "--model", alias, "--output-format", "json", "--max-turns", "1", "--tools", "",
                 "--strict-mcp-config", "--no-session-persistence", "--setting-sources", ""],
                input="Reply with just OK", capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=120, cwd=cwd, env=env, **procutil.NO_WINDOW,
            ).stdout
        usage = (json.loads(out or "{}").get("modelUsage") or {})
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    hits = [k for k in usage if (parse(k) or ("", "", ()))[1] == alias]
    return max(hits, key=lambda k: parse(k)[2]) if hits else None


def _claude_latest(prev: dict) -> dict:
    out = dict(prev)
    threads, found = [], {}

    def run(alias):
        mid = _claude_resolve(alias)
        if mid:
            found[alias] = mid
    for a in ANTHROPIC_FAMILIES:
        t = threading.Thread(target=run, args=(a,), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join(150)
    for fam, mid in found.items():
        out[fam] = {"id": mid, "name": display_name(mid)}
    return out


def refresh(force: bool = False) -> dict:
    """Re-read both lists now (blocking). One process at a time; a lock older than 10 minutes is stale."""
    data = _read_cache()
    if not force and time.time() - data.get("checked_at", 0) < MAX_AGE:
        return data
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    try:
        if LOCK.exists() and time.time() - LOCK.stat().st_mtime > 600:
            LOCK.unlink()
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except OSError:
        return data
    try:
        new = {"checked_at": time.time(),
               "anthropic": _claude_latest(data.get("anthropic") or {}) if Path(procutil.local_bin("claude")).exists() else data.get("anthropic") or {},
               "openai": _codex_latest() or data.get("openai") or {}}
        tmp = CACHE.with_suffix(".tmp")
        tmp.write_text(json.dumps(new, indent=1), encoding="utf-8")
        tmp.replace(CACHE)
        _mem.update(data=None, mtime=0.0)
        return new
    finally:
        try:
            LOCK.unlink()
        except OSError:
            pass


def refresh_soon() -> None:
    if os.environ.get("HMEM_NO_LLM") == "1":  # tests / offline: never spawn a CLI
        return
    if time.time() - _read_cache().get("checked_at", 0) < MAX_AGE or not _busy.acquire(blocking=False):
        return

    def go():
        try:
            refresh()
        finally:
            _busy.release()
    threading.Thread(target=go, daemon=True, name="model-latest").start()


def families() -> dict:
    d = _read_cache()
    oa = _codex_latest() or d.get("openai") or {}  # the Codex file is local and cheap: always current
    return {"anthropic": d.get("anthropic") or {}, "openai": oa}


def upgrade(model_id: str | None) -> str | None:
    """claude-fable-5-1 -> claude-fable-5-2 once 5.2 is known; never downgrades, unknown ids pass through."""
    p = parse(model_id or "")
    if not p:
        return model_id
    new = (families().get(p[0]) or {}).get(p[1])
    if not new:
        return model_id
    np = parse(new["id"])
    return new["id"] if np and np[2] > p[2] else model_id
