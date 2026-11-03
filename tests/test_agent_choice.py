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
    out = cc._route_motion("s1", "/motion advertise to students", "/motion advertise to students")
    assert started and "isn't installed" in out and "installing Node.js" in out and "Do not call any tools" in out


def test_motion_render_steps_run_on_claude(monkeypatch, tmp_path):
    """Codex's sandbox can't start Remotion's Chrome: studio render/revision steps go to Claude Code,
    other studio steps (storyboard, music) and other folders stay on the picked model."""
    monkeypatch.setenv("TABBIE_MOTION_STUDIO", str(tmp_path / "motion"))
    assert cc._in_motion_studio(str(tmp_path / "motion" / "studio" / "film"))
    assert not cc._in_motion_studio(str(tmp_path / "app"))

    class R:
        def __init__(self, text):
            self.data = {"raw_message": text}
    job = lambda kind: f'/motion [studio:{kind} job={kind}-1] Motion studio project "Ad". render it, rendered frames'  # noqa: E731
    assert cc._renders(R(job("render"))) and cc._renders(R(job("revision")))
    assert not any(cc._renders(R(job(k))) for k in ("storyboard", "analyze", "generate-audio"))
    assert cc._renders(R("can you re-render the ending")) and not cc._renders(R("make the logo bigger"))
