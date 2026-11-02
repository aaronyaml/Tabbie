"""Limits gate: nothing starts on an account at its limit (plugin/command-centre/limits_gate.py)."""
from __future__ import annotations
import os
import json, subprocess, sys
from pathlib import Path

import pytest

from cc_helpers import gate, wire

NOW = 1_800_000_000.0
CFG = {"limits": {"block_at_pct": 95, "reserve_for_hub_pct": 90}}


def claude(session=50, week=10, opus=None, fable=None):
    rows = [{"service": "Claude Max 5x", "window": "5h session", "pct": session, "resets_at": NOW + 3600, "note": ""},
            {"service": "Claude Max 5x", "window": "7d all models", "pct": week, "resets_at": NOW + 86400, "note": ""}]
    if opus is not None:
        rows.append({"service": "Claude Max 5x", "window": "7d Opus", "pct": opus, "resets_at": NOW + 86400, "note": ""})
    if fable is not None:
        rows.append({"service": "Claude Max 5x", "window": "7d Fable", "pct": fable, "resets_at": NOW + 86400, "note": ""})
    return lambda agent: rows


def codex(pct5=10, pct7=10, shared=True, note=""):
    svc = "Codex + Hub (shared pool)" if shared else "ChatGPT Plus · Codex gpt-6-astra"
    rows = [{"service": f"ChatGPT Plus · {svc}" if shared else svc, "window": "5h", "pct": pct5,
             "resets_at": NOW + 1800, "note": note},
            {"service": f"ChatGPT Plus · {svc}" if shared else svc, "window": "7d", "pct": pct7,
             "resets_at": NOW + 86400 * 3, "note": note}]
    if not shared:
        rows.append({"service": "ChatGPT Plus · Hub gpt-6.1-sol", "window": "5h", "pct": 99, "resets_at": NOW, "note": ""})
    return lambda agent: rows


@pytest.fixture()
def g(tmp_path, monkeypatch):
    monkeypatch.delenv("TABBIE_LIMITS_GATE", raising=False)
    wire(tmp_path, monkeypatch)
    return gate


def test_thresholds(g):
    assert g.check("claude", "claude-opus-5-5", cfg=CFG, now=NOW, fetch=claude(94.9)).state == "ok"
    b = g.check("claude", "claude-opus-5-5", cfg=CFG, now=NOW + 61, fetch=claude(97))
    assert b.blocked and b.pct == 97 and b.window == "5h session"
    assert b.message.startswith("Claude is at 97% of its 5-hour window (resets ")


def test_model_scoped_weekly_windows(g):
    # 7d Opus counts for Opus, 7d Fable does not (and the reverse)
    assert g.check("claude", "claude-opus-5-5", cfg=CFG, now=NOW, fetch=claude(opus=99), refresh=True).blocked
    assert not g.check("claude", "claude-sonnet-5-5", cfg=CFG, now=NOW, fetch=claude(opus=99), refresh=True).blocked
    assert not g.check("claude", "claude-opus-5-5", cfg=CFG, now=NOW, fetch=claude(fable=99), refresh=True).blocked
    assert g.check("claude", "claude-fable-5-1", cfg=CFG, now=NOW, fetch=claude(fable=99), refresh=True).blocked


def test_codex_shared_pool_and_hub_reserve(g):
    f = codex(pct5=92)
    assert not g.check("codex", "gpt-6-astra", purpose="work", cfg=CFG, now=NOW, fetch=f, refresh=True).blocked
    d = g.check("codex", "gpt-6-astra", purpose="delegate", cfg=CFG, now=NOW, fetch=f, refresh=True)
    assert d.blocked and "Codex is at 92%" in d.message  # hand-offs keep the hub's shared pool
    # a hub-only row (separate login) never limits Codex
    assert not g.check("codex", "gpt-6-astra", cfg=CFG, now=NOW, fetch=codex(shared=False), refresh=True).blocked
    # explicit limit / blocked model notes
    assert g.check("codex", "gpt-6-astra", cfg=CFG, now=NOW, fetch=codex(note="limit reached"), refresh=True).blocked
    assert g.check("codex", "gpt-6-astra", cfg=CFG, now=NOW, fetch=codex(note="blocked: gpt-6-astra"), refresh=True).blocked
    assert not g.check("codex", "gpt-6-luna", cfg=CFG, now=NOW, fetch=codex(note="blocked: gpt-6-astra"), refresh=True).blocked


def test_unknown_fails_open(g):
    def boom(agent):
        raise RuntimeError("HTTP 500")
    assert g.check("claude", None, cfg=CFG, now=NOW, fetch=boom).state == "unknown"
    nodata = lambda a: [{"service": "Claude Max", "window": "—", "pct": None, "note": "usage endpoint returned no limits"}]
    assert g.check("claude", None, cfg=CFG, now=NOW, fetch=nodata, refresh=True).state == "unknown"
    old = lambda a: [{"service": "ChatGPT · Codex gpt-6-astra", "window": "5h", "pct": 99, "stale_seconds": 7200,
                      "resets_at": NOW + 60, "note": ""}]
    assert not g.check("codex", None, cfg=CFG, now=NOW, fetch=old, refresh=True).blocked  # too old to trust


def test_shared_file_cache_ttl(g, tmp_path):
    calls = []

    def f(agent):
        calls.append(agent)
        return claude(97)(agent)
    g.check("claude", None, cfg=CFG, now=NOW, fetch=f)
    g.check("claude", None, cfg=CFG, now=NOW + 30, fetch=f)
    assert calls == ["claude"]  # second check within 60s: from the file
    cached = json.loads((tmp_path / "limits_cache.json").read_text(encoding="utf-8"))
    assert cached["claude"]["at"] == NOW and "token" not in json.dumps(cached).lower()
    g.check("claude", None, cfg=CFG, now=NOW + 61, fetch=f)
    assert calls == ["claude", "claude"]
    # a failed refresh keeps the last rows, which still count while fresh enough
    def boom(agent):
        raise RuntimeError("down")
    assert g.check("claude", None, cfg=CFG, now=NOW + 200, fetch=boom).blocked
    assert g.check("claude", None, cfg=CFG, now=NOW + 61 + 16 * 60, fetch=boom).state == "unknown"


def test_message_text(g):
    assert g.window_name("7d Opus") == "weekly Opus limit" and g.window_name("5h") == "5-hour window"
    b = g.check("codex", None, cfg=CFG, now=NOW, fetch=codex(pct7=97), refresh=True)
    assert b.message.startswith("Codex is at 97% of its weekly limit (resets ")


def test_limit_gate_cli_exit_codes(tmp_path):
    cache = tmp_path / "state" / "limits_cache.json"
    cache.parent.mkdir(parents=True)
    import time
    rows = claude(98)("claude")
    cache.write_text(json.dumps({"claude": {"at": time.time(), "rows": rows}}), encoding="utf-8")
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "USERPROFILE": str(tmp_path), "COMMAND_CENTRE_HOME": str(tmp_path),
           **{k: os.environ[k] for k in ("SYSTEMROOT", "TEMP", "TMP") if k in os.environ}}  # Windows needs these
    bin_ = Path(__file__).resolve().parents[1] / "bin" / "limit-gate"
    p = subprocess.run([sys.executable, str(bin_), "claude", "--model", "claude-opus-5-5"], env=env,
                       input="{}", capture_output=True, text=True, timeout=30)
    assert p.returncode == 2 and "98%" in p.stderr and "inline" in p.stderr
    cache.write_text(json.dumps({"claude": {"at": time.time(), "rows": claude(40)("claude")}}), encoding="utf-8")
    p = subprocess.run([sys.executable, str(bin_), "claude"], env=env, input="{}", capture_output=True, text=True, timeout=30)
    assert p.returncode == 0
