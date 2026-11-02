"""Limits gate: is there enough subscription usage left to START a worker, delegate or subagent?

Checked before anything is spawned (Claude Code worker, Codex worker, planner, a dynamic-agent
hand-off, a native subagent through bin/limit-gate) so a job never starts on an account that is
already at its limit.

Reads only the fast sources of plugin/limits (Claude's oauth/usage, ChatGPT's wham/usage), never
the slow Higgsfield / ElevenLabs CLIs. Results are shared through a small file cache
(state/limits_cache.json, CACHE_TTL seconds) by the gateway, the sidecar, delegate servers and hooks.

check() -> Gate(state):
  ok       every applicable window is below the threshold
  blocked  a window is >= limits.block_at_pct (default 95) or the row says "limit reached" / "blocked: <model>"
  unknown  no data, an unreadable endpoint or data older than STALE_S: fails OPEN (logged), the job runs
Windows that apply:
  claude  5h session, 7d all models, and a 7d model window only for the selected model's family
          (7d Opus counts for Opus, 7d Fable does not)
  codex   the Codex rows (often the pool shared with the hub). Delegations stop earlier, at
          limits.reserve_for_hub_pct, so a hand-off never eats the hub's own budget.
"""

from __future__ import annotations

import importlib.util
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .flockcompat import fcntl
from .common import STATE_DIR, load_config, log_decision, read_json, write_json

CACHE_FILE = STATE_DIR / "limits_cache.json"
CACHE_TTL = 60
STALE_S = 15 * 60
LABEL = {"claude": "Claude", "codex": "Codex"}
_LIMITS_INIT = Path(__file__).resolve().parents[1] / "limits" / "__init__.py"
_mod = {"m": None}


def limits_module():
    """plugin/limits loaded by path (it is a Hermes plugin, not an importable package)."""
    if _mod["m"] is None:
        spec = importlib.util.spec_from_file_location("tabbie_limits_plugin", _LIMITS_INIT)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        _mod["m"] = m
    return _mod["m"]


def _fetch(agent: str) -> list[dict]:
    m = limits_module()
    return m.claude_rows() if agent == "claude" else m.codex_rows()


def rows(agent: str, *, refresh: bool = False, now: float | None = None, fetch=None) -> tuple[list[dict] | None, float | None]:
    """(rows, fetched_at) for claude|codex from the shared file cache, refetched when older than CACHE_TTL.
    A failed fetch keeps the previous rows (their age decides whether they still count)."""
    now = time.time() if now is None else now
    cache = read_json(CACHE_FILE, {}) or {}
    hit = cache.get(agent) or {}
    if not refresh and hit.get("rows") is not None and now - float(hit.get("at") or 0) < CACHE_TTL:
        return hit["rows"], float(hit["at"])
    try:
        got = (fetch or _fetch)(agent)
    except Exception as exc:
        log_decision(event="limits_fetch_failed", agent=agent, error=type(exc).__name__)
        return hit.get("rows"), (float(hit["at"]) if hit.get("at") else None)
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_FILE.with_suffix(".lock"), "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        cache = read_json(CACHE_FILE, {}) or {}
        cache[agent] = {"at": now, "rows": got}
        write_json(CACHE_FILE, cache)
    return got, now


@dataclass
class Gate:
    agent: str
    state: str                       # ok | blocked | unknown
    pct: float | None = None
    window: str | None = None
    resets_at: float | None = None
    message: str = ""
    details: list[dict] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return self.state == "blocked"

    def as_dict(self) -> dict:
        return {"agent": self.agent, "state": self.state, "pct": self.pct, "window": self.window,
                "resets_at": self.resets_at, "message": self.message}


_WINDOW = {"5h session": "5-hour window", "5h": "5-hour window", "7d all models": "weekly limit",
           "7d": "weekly limit", "extra usage (month)": "extra usage"}


def window_name(window: str) -> str:
    if window in _WINDOW:
        return _WINDOW[window]
    m = re.match(r"7d (.+)", window or "")
    return f"weekly {m.group(1)} limit" if m else (window or "limit")


def clock(epoch: float | None, now: float | None = None) -> str:
    if not epoch:
        return ""
    now = time.time() if now is None else now
    t, today = datetime.fromtimestamp(epoch), datetime.fromtimestamp(now)
    hm = t.strftime("%I:%M%p").lstrip("0").lower()
    days = (t.date() - today.date()).days
    if days <= 0:
        return hm
    if days == 1:
        return f"tomorrow {hm}"
    return f"{t.strftime('%a')} {hm}"


def _family(model: str | None) -> str | None:
    m = re.search(r"(opus|sonnet|haiku|fable)", str(model or ""), re.I)
    return m.group(1).lower() if m else None


def applicable(agent: str, rows_: list[dict], model: str | None = None) -> list[dict]:
    out = []
    fam = _family(model)
    for r in rows_ or []:
        svc, win = str(r.get("service") or ""), str(r.get("window") or "")
        if agent == "claude":
            if not svc.startswith("Claude") or win.startswith("extra usage"):
                continue
            mm = re.match(r"7d (.+)", win)
            if mm and win != "7d all models":
                if mm.group(1).strip().lower() in ("oauth apps",):
                    continue
                if not fam or fam not in mm.group(1).lower():
                    continue  # another model's weekly window: not this job's limit
            out.append(r)
        else:
            if "Codex" not in svc:  # the hub-only row (separate login) does not limit Codex
                continue
            out.append(r)
    return out


def check(agent: str, model: str | None = None, *, purpose: str = "work", cfg: dict | None = None,
          now: float | None = None, fetch=None, refresh: bool = False) -> Gate:
    """purpose: work (a worker or planner the user asked for) | delegate | subagent."""
    if os.environ.get("TABBIE_LIMITS_GATE") == "off" and fetch is None:  # tests / bench dry runs
        return Gate(agent, "ok", message="limits gate off")
    now = time.time() if now is None else now
    lcfg = ((cfg if cfg is not None else _config()).get("limits") or {})
    block_at = float(lcfg.get("block_at_pct", 95))
    reserve = float(lcfg.get("reserve_for_hub_pct", 90))
    got, at = rows(agent, refresh=refresh, now=now, fetch=fetch)
    who = LABEL.get(agent, agent)
    if got is None or at is None or now - at > STALE_S:
        g = Gate(agent, "unknown", message=f"{who} usage unknown (no recent data); starting anyway")
        log_decision(event="limits_unknown", agent=agent, purpose=purpose)
        return g
    rel = applicable(agent, got, model)
    if not rel or all(r.get("pct") is None and not r.get("note") for r in rel):
        return Gate(agent, "unknown", message=f"{who} usage unknown; starting anyway")
    worst: dict | None = None
    unknown = True
    for r in rel:
        note = str(r.get("note") or "")
        if r.get("stale_seconds") is not None and float(r["stale_seconds"]) > STALE_S:
            continue  # Codex session-log fallback that is too old to trust
        thr = block_at
        if agent == "codex" and purpose == "delegate" and "Hub" in str(r.get("service") or ""):
            thr = min(block_at, reserve)
        bad_model = re.search(r"blocked: (.+)", note)
        hard = "limit reached" in note.lower() or (
            bad_model and (not model or model in bad_model.group(1)))
        pct = r.get("pct")
        if pct is not None or hard:
            unknown = False
        if hard or (pct is not None and float(pct) >= thr):
            if worst is None or float(pct or 100) > float(worst.get("pct") or 0):
                worst = {**r, "pct": float(pct) if pct is not None else 100.0, "_thr": thr}
    if worst:
        when = clock(worst.get("resets_at"), now)
        msg = (f"{who} is at {worst['pct']:g}% of its {window_name(worst.get('window'))}"
               + (f" (resets {when})" if when else ""))
        return Gate(agent, "blocked", worst["pct"], worst.get("window"), worst.get("resets_at"), msg, rel)
    if unknown:
        return Gate(agent, "unknown", message=f"{who} usage unknown; starting anyway", details=rel)
    top = max((r for r in rel if r.get("pct") is not None), key=lambda r: float(r["pct"]))
    return Gate(agent, "ok", float(top["pct"]), top.get("window"), top.get("resets_at"),
                f"{who} at {float(top['pct']):g}% of its {window_name(top.get('window'))}", rel)


def _config() -> dict:
    try:
        return load_config()
    except Exception:  # no YAML library (system python in a hook): the defaults
        return {}


def agent_of(provider: str) -> str:
    """executor_choice provider (anthropic|openai) or planner provider (claude-cli|codex-cli) -> claude|codex."""
    p = str(provider or "")
    return "codex" if p in ("openai", "codex", "codex-cli") or p.startswith("codex") else "claude"


def other(agent: str) -> str:
    return "codex" if agent == "claude" else "claude"
