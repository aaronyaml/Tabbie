"""Shared setup for the command-centre tests: load the plugin package and point its state at a temp dir."""
from __future__ import annotations
import importlib.util, sys
from importlib import import_module
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "plugin" / "command-centre"
if "command_centre" not in sys.modules:
    spec = importlib.util.spec_from_file_location("command_centre", PKG / "__init__.py",
                                                  submodule_search_locations=[str(PKG)])
    _m = importlib.util.module_from_spec(spec); sys.modules["command_centre"] = _m
    spec.loader.exec_module(_m)
cc = sys.modules["command_centre"]
st = import_module("command_centre.state")
ag = import_module("command_centre.agents")
common = import_module("command_centre.common")
prov = import_module("command_centre.providers")
gate = import_module("command_centre.limits_gate")
wp = import_module("command_centre.worker_profile")
ec = import_module("command_centre.executor_choice")


def wire(tmp: Path, monkeypatch, cfg: dict | None = None) -> Path:
    """Every state path under tmp; load_config returns cfg; no decision log noise."""
    req = tmp / "req"
    for mod in (cc, st, ag, common):
        monkeypatch.setattr(mod, "REQUESTS_DIR", req, raising=False)
    monkeypatch.setattr(st, "SESSIONS_DIR", tmp / "ses")
    monkeypatch.setattr(cc, "LOCKS_DIR", tmp / "locks")
    monkeypatch.setattr(prov, "LOCKS_DIR", tmp / "locks")
    monkeypatch.setattr(ag, "AGENTS_INDEX_DIR", tmp / "agents")
    monkeypatch.setattr(cc, "_CLAUDE_SESSIONS", tmp / "cs.json")
    monkeypatch.setattr(cc, "log_decision", lambda **k: None)
    monkeypatch.setattr(gate, "log_decision", lambda **k: None)
    monkeypatch.setattr(gate, "CACHE_FILE", tmp / "limits_cache.json")
    monkeypatch.setattr(wp, "WORKER_DIR", tmp / "worker")
    monkeypatch.setattr(wp, "SETTINGS_FILE", tmp / "worker" / "claude-settings.json")
    monkeypatch.setattr(wp, "SKILLS_PLUGIN", tmp / "worker" / "user-skills")
    conf = cfg if cfg is not None else {"executor": {}}
    monkeypatch.setattr(cc, "load_config", lambda: conf)
    return req


def request(text="do it", sid="s1", project=None, route="claude"):
    return st.Request.create(session_id=sid, route=route, reason="scoped", forced=False, prompt=text,
                             raw_message=text, project=str(project) if project else None, project_source="t")
