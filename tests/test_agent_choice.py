"""Claude Code only, Codex only, or both (config/agents.yaml): setting, catalog, planner, takeover."""
from __future__ import annotations
import importlib.util, sys, tempfile
from importlib import import_module
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "plugin" / "command-centre"
if "command_centre" not in sys.modules:
    spec = importlib.util.spec_from_file_location("command_centre", PKG / "__init__.py",
                                                  submodule_search_locations=[str(PKG)])
    _m = importlib.util.module_from_spec(spec); sys.modules["command_centre"] = _m
    spec.loader.exec_module(_m)
cc = sys.modules["command_centre"]
ec = import_module("command_centre.executor_choice")
st = import_module("command_centre.state")
prov = import_module("command_centre.providers")
from hmem import agents  # noqa: E402


def _tmp_state():
    d = Path(tempfile.mkdtemp())
    st.REQUESTS_DIR, st.SESSIONS_DIR = d / "req", d / "ses"


def test_default_is_both_and_save_round_trips():
    assert agents.enabled() == ["claude", "codex"] and not agents.path().exists()
    assert agents.save(["Codex"]) == ["codex"]
    assert agents.enabled() == ["codex"] and agents.has("codex") and not agents.has("claude")
    assert "enabled: [codex]" in agents.path().read_text(encoding="utf-8")
    assert agents.save(["codex", "claude", "nope"]) == ["claude", "codex"]  # fixed order, unknowns dropped
    with pytest.raises(ValueError):
        agents.save([])
    with pytest.raises(ValueError):
        agents.save(["gpt"])
    assert agents.enabled() == ["claude", "codex"]  # a refused save leaves the file alone


def test_small_commits_and_tests_default_on_and_switch_off():
    assert agents.step_commits_enabled() and agents.uat_enabled()  # no file: on
    agents.save(step_commits=False)
    assert not agents.step_commits_enabled() and agents.uat_enabled() and agents.takeover_enabled()
    agents.save(uat=False)
    assert not agents.step_commits_enabled() and not agents.uat_enabled()
    on = prov.worker_rules("claude")
    assert "SMALL COMMITS" not in on and "TEST BEFORE DONE" not in on
    agents.save(step_commits=True, uat=True)
    rules = prov.worker_rules("codex", dynamic=True)
    assert "SMALL COMMITS" in rules and "git add -A" in rules and "TEST BEFORE DONE" in rules
    assert rules == prov.worker_rules("codex", dynamic=True)  # still byte-identical: the prefix caches


def test_broken_file_falls_back_to_both():
    agents.path().parent.mkdir(parents=True, exist_ok=True)
    for text in ("", "enabled: []\n", "enabled: [banana]\n", "garbage"):
        agents.path().write_text(text, encoding="utf-8")
        assert agents.enabled() == ["claude", "codex"], text


def test_codex_only_catalog_and_default():
    _tmp_state()
    agents.save(["codex"])
    cat = ec.public_catalog([])
    assert list(cat["providers"]) == ["openai"] and cat["agents"] == ["codex"]
    assert cat["default"]["provider"] == "openai"
    assert ec.get_choice("fresh")["provider"] == "openai"
    with pytest.raises(ValueError):
        ec.set_choice("s", "anthropic", "claude-opus-5-5")


def test_turning_an_agent_off_moves_the_stored_pick():
    _tmp_state()
    ec.set_choice("s", "openai", "gpt-6.1-sol")
    agents.save(["claude"])
    ch = ec.get_choice("s")
    assert ch["provider"] == "anthropic" and ch["model"] == "claude-opus-5-5"
    assert list(ec.public_catalog([])["providers"]) == ["anthropic"]
    agents.save(["claude", "codex"])
    assert ec.get_choice("s")["model"] == "gpt-6.1-sol"  # the pick comes back when Codex is on again


def test_planner_follows_the_enabled_agent():
    cfg = {"planner": {"provider": "codex-cli", "model": "gpt-6-astra", "label": "Astra"}}
    assert prov.planner_config(cfg)["provider"] == "codex-cli"
    agents.save(["claude"])
    p = prov.planner_config(cfg)
    assert p["provider"] == "claude-cli" and p["model"] is None
    assert cfg["planner"]["provider"] == "codex-cli"  # the config dict itself is untouched
    agents.save(["codex"])
    p = prov.planner_config({"planner": {"provider": "claude-cli", "model": "claude-opus-5-5"}})
    assert (p["provider"], p["model"]) == ("codex-cli", "gpt-6-astra")


def test_takeover_needs_codex():
    _tmp_state()
    agents.save(["claude"])
    out = str(cc._cmd_takeover("", session_id="agent-test"))
    assert "needs Codex" in out
    assert not st.session_state("agent-test").get("takeover")


def test_motion_command_installs_motion_first(monkeypatch):
    from hmem import motion
    started = []
    monkeypatch.setattr(motion, "installed", lambda p=None: False)
    monkeypatch.setattr(motion, "install", lambda: started.append(1) or {"state": "running", "step": "node", "error": ""})
    monkeypatch.setattr(cc, "log_decision", lambda **kw: None)
    monkeypatch.setattr(cc, "motion_ready", lambda: True)
    out = cc._route_motion("s1", "/motion advertise to students", "/motion advertise to students")
    assert started and "isn't installed" in out and "installing Node.js" in out and "Do not call any tools" in out


def test_motion_needs_claude(monkeypatch):
    """Motion runs on Claude Code only: without Claude linked nothing starts (no install, no request)."""
    from hmem import motion
    monkeypatch.setattr(cc, "motion_ready", lambda: False)
    monkeypatch.setattr(motion, "install", lambda: (_ for _ in ()).throw(AssertionError("installed")))
    monkeypatch.setattr(cc, "log_decision", lambda **kw: None)
    out = cc._route_motion("s1", "/motion advertise", "/motion advertise")
    assert "Claude" in out and "Nothing was started" in out and "Do not call any tools" in out


def test_motion_studio_folder(monkeypatch, tmp_path):
    monkeypatch.setenv("TABBIE_MOTION_STUDIO", str(tmp_path / "motion"))
    assert cc._in_motion_studio(str(tmp_path / "motion" / "studio" / "film"))
    assert not cc._in_motion_studio(str(tmp_path / "app"))


def test_motion_pause_and_continue(monkeypatch, tmp_path):
    """At a usage limit a Motion step pauses: the request keeps its Claude session, the step's job file
    says paused, and a "Continuing paused step <id>" message in the same chat finds that request."""
    import json
    st = sys.modules[cc.Request.__module__]
    studio = tmp_path / "motion"
    monkeypatch.setenv("TABBIE_MOTION_STUDIO", str(studio))
    monkeypatch.setattr(cc, "REQUESTS_DIR", tmp_path / "req")
    monkeypatch.setattr(st, "REQUESTS_DIR", tmp_path / "req")
    monkeypatch.setattr(cc, "log_decision", lambda **kw: None)
    film = studio / "studio" / "ad"
    mk = lambda sid, raw: cc.Request.create(session_id=sid, route=cc.CLAUDE, reason="motion", forced=True,  # noqa: E731
                                            prompt="p", raw_message=raw, project=str(studio), project_source="/motion")
    r = mk("s1", f'/motion [studio:render job=render-abc] Motion studio project "Ad" (slug ad; its files are in {film}/). '
                 "Build and render the film now.")
    r["executor"] = {"session_id": "claude-sess-1"}
    r.save()
    out = cc._studio_pause(r, "Claude hit its usage limit (resets 9pm)", started=True)
    assert out["paused"] and not out["ok"] and "Continue" in out["note"]
    job = json.loads((film / "jobs" / "render-abc.json").read_text())
    assert job["status"] == "paused" and "resets 9pm" in job["note"] and job["request"] == r.id
    assert cc.Request(r.id)["status"] == "paused"
    found = cc._paused_for(mk("s1", "/motion [studio:render job=render-def] Continuing paused step render-abc: carry on"))
    assert found and found["id"] == r.id and found["executor"]["session_id"] == "claude-sess-1"
    assert cc._paused_for(mk("s2", "/motion [studio:render job=x] Continuing paused step render-abc")) is None
    assert cc._paused_for(mk("s1", "/motion [studio:render job=y] Build it")) is None
