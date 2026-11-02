"""Tabbie updates: settings, the check (no download), what an update needs restarted."""
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
up = import_module("command_centre.updates")

A, B = "a" * 40, "b" * 40


def _root(tmp_path, monkeypatch, router=""):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "router.yaml").write_text(router, encoding="utf-8")
    monkeypatch.setattr(up, "ROOT", tmp_path)
    monkeypatch.setattr(up, "STATE", tmp_path / "state" / "updates.json")
    up._cache.update(at=0.0, result=None)


def test_settings_from_router_yaml(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch, "planner:\n  model: x\nupdates:\n  repo: someone/Fork  # mine\n"
                                  "  branch: dev\n  check: false\nmemory:\n  repo: nope/nope\n")
    assert up.settings() == {"repo": "someone/Fork", "branch": "dev", "check": False}


def test_bad_values_fall_back(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch, "updates:\n  repo: x; rm -rf\n  branch: a..b\n")
    assert up.settings()["repo"] == "aaronyaml/Tabbie" and up.settings()["branch"] == "main"


def _fake_git(local, remote):
    def git(*args, timeout=30):
        if args[:1] == ("rev-parse",):
            return local
        if args[:1] == ("ls-remote",):
            return f"{remote}\trefs/heads/main"
        raise AssertionError(args)  # the check must never fetch or merge
    return git


def test_check_offers_newer_and_never_downloads(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    monkeypatch.setattr(up, "_git", _fake_git(A, B))
    monkeypatch.setattr(up, "_changes", lambda r, b, h: {"status": "ahead", "ahead_by": 2,
                                                          "commits": [{"sha": "b", "title": "two"}, {"sha": "a", "title": "one"}]})
    r = up.check(force=True)
    assert r["available"] and r["ahead_by"] == 2 and r["commits"][0]["title"] == "two"


def test_check_up_to_date_and_local_only_changes(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    monkeypatch.setattr(up, "_git", _fake_git(A, A))
    assert up.check(force=True)["available"] is False
    monkeypatch.setattr(up, "_git", _fake_git(A, B))
    for status in ("behind", "diverged"):
        monkeypatch.setattr(up, "_changes", lambda r, b, h, s=status: {"status": s, "ahead_by": 0, "commits": []})
        r = up.check(force=True)
        assert r["available"] is False and r["note"]


def test_check_offline_says_so(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    def git(*args, timeout=30):
        if args[0] == "ls-remote":
            raise RuntimeError("fatal: unable to access\nCould not resolve host: github.com")
        return A
    monkeypatch.setattr(up, "_git", git)
    r = up.check(force=True)
    assert r["available"] is False and "github.com" in r["note"]


def test_needs():
    assert up._needs(["webui-extension/x.js"]) == ["reload"]
    assert up._needs(["plugin/command-centre/router.py"]) == ["gateway", "sidecar", "webui"]
    assert up._needs(["services/Caddyfile", "bin/setup", "README.md"]) == ["caddy", "setup"]


def test_restart_waits_until_idle(tmp_path, monkeypatch):
    import time as _t
    _root(tmp_path, monkeypatch)
    up._save_state({"needs": ["gateway"]})
    state = {"busy": True}
    restarted = []
    monkeypatch.setattr(up, "busy", lambda: state["busy"])
    monkeypatch.setattr(up, "restart", lambda after_reply=None: restarted.append(1) or up._save_state({}) or {"ok": True})
    monkeypatch.setattr(up, "POLL", 0.02)
    monkeypatch.setattr(up, "IDLE_GRACE", 0.06)
    up._waiter["thread"] = None
    assert up.restart_when_idle() == {"ok": True, "waiting": True}
    assert up.waiting()
    _t.sleep(0.15)
    assert not restarted  # a Motion render / worker is still running
    state["busy"] = False
    _t.sleep(0.3)
    assert restarted == [1] and not up.waiting()


def test_restart_now_when_idle(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    up._save_state({"needs": ["gateway"]})
    monkeypatch.setattr(up, "busy", lambda: False)
    monkeypatch.setattr(up, "restart", lambda after_reply=None: {"ok": True, "restarted": ["gateway"]})
    assert up.restart_when_idle() == {"ok": True, "restarted": ["gateway"]}


def _services(tmp_path, monkeypatch, loaded):
    """Fake services/ dir and launchctl: `loaded` is the set of labels launchd knows."""
    svc = tmp_path / "services"; svc.mkdir()
    for label in ("ai.hermes.webui", "ai.hermes.cc-sidecar"):
        (svc / f"{label}.plist.template").write_text(f"<plist>{label} __HOME__/hermes</plist>", encoding="utf-8")
    agents = tmp_path / "LaunchAgents"
    calls = []

    def launchctl(*args):
        calls.append(args)
        if args[0] == "print":
            rc = 0 if args[1].rsplit("/", 1)[-1] in loaded else 113
        else:
            rc = 0
            if args[0] == "bootstrap":
                loaded.add(Path(args[2]).name[: -len(".plist")])
        return type("P", (), {"returncode": rc})()
    monkeypatch.setattr(up, "SERVICES", svc)
    monkeypatch.setattr(up, "AGENTS_DIR", agents)
    monkeypatch.setattr(up, "_launchctl", launchctl)
    monkeypatch.setattr(up.sys, "platform", "darwin")
    monkeypatch.delenv("TABBIE_NO_SERVICES", raising=False)
    return agents, calls


def test_ensure_services_installs_only_missing(tmp_path, monkeypatch):
    agents, calls = _services(tmp_path, monkeypatch, {"ai.hermes.webui"})
    assert up.ensure_services() == ["ai.hermes.cc-sidecar"]
    written = (agents / "ai.hermes.cc-sidecar.plist").read_text(encoding="utf-8")
    assert "__HOME__" not in written and str(Path.home()) in written
    assert not (agents / "ai.hermes.webui.plist").exists()  # running service untouched
    assert up.ensure_services() == []  # idempotent


def test_ensure_services_skips_when_setup_never_installed_them(tmp_path, monkeypatch):
    agents, calls = _services(tmp_path, monkeypatch, set())
    assert up.ensure_services() == []
    assert not agents.exists()


def test_ensure_services_off_in_tests_and_off_mac(tmp_path, monkeypatch):
    _services(tmp_path, monkeypatch, {"ai.hermes.webui"})
    monkeypatch.setenv("TABBIE_NO_SERVICES", "1")
    assert up.ensure_services() == []
    monkeypatch.delenv("TABBIE_NO_SERVICES")
    monkeypatch.setattr(up.sys, "platform", "linux")
    assert up.ensure_services() == []


def test_changed_service_waits_for_restart(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    agents, calls = _services(tmp_path, monkeypatch, {"ai.hermes.webui", "ai.hermes.cc-sidecar"})
    up._save_state({"needs": [], "services": ["ai.hermes.cc-sidecar"]})
    assert "services" in up.pending_restart()
    popen = []
    monkeypatch.setattr(up, "busy", lambda: False)
    monkeypatch.setattr(up.subprocess, "Popen", lambda *a, **k: popen.append(a[0]))
    res = up.restart()
    assert res["ok"] and "ai.hermes.cc-sidecar" in res["restarted"]
    assert (agents / "ai.hermes.cc-sidecar.plist").exists() and "bootstrap" in popen[0][-1]
    assert up.pending_restart() == []


def _req(root, rid, status, age, agents=None):
    import json, os, time
    d = root / "requests" / rid
    d.mkdir(parents=True)
    (d / "request.json").write_text(json.dumps({"id": rid, "status": status, "result": None}), encoding="utf-8")
    if agents is not None:
        (d / "agents.json").write_text(json.dumps({"agents": agents, "summary": {"running": 1, "failed": 0}}), encoding="utf-8")
    t = time.time() - age
    for f in d.iterdir():
        os.utime(f, (t, t))
    return d


def test_orphaned_runs_are_settled(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(up, "STATE_DIR", tmp_path)
    monkeypatch.setattr(up, "_workers_alive", lambda: False)
    old = _req(tmp_path, "20261102-100000-aaaaaa", "executing", 600,
               [{"id": "claude-1", "status": "running", "started": 1.0}, {"id": "bg", "status": "done"}])
    fresh = _req(tmp_path, "20261102-100100-bbbbbb", "executing", 5)
    done = _req(tmp_path, "20261102-100200-cccccc", "done", 600)
    assert up.reap_orphans() == [old.name]
    req = json.loads((old / "request.json").read_text(encoding="utf-8"))
    assert req["status"] == "failed" and "restarted" in req["result"]["error"]
    ag = json.loads((old / "agents.json").read_text(encoding="utf-8"))
    assert [a["status"] for a in ag["agents"]] == ["failed", "done"] and ag["summary"]["running"] == 0
    assert json.loads((fresh / "request.json").read_text(encoding="utf-8"))["status"] == "executing"  # may still be starting
    assert json.loads((done / "request.json").read_text(encoding="utf-8"))["status"] == "done"
    assert up.reap_orphans() == []


def test_orphans_left_alone_while_any_worker_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(up, "STATE_DIR", tmp_path)
    monkeypatch.setattr(up, "_workers_alive", lambda: True)
    _req(tmp_path, "20261102-100000-aaaaaa", "executing", 600)
    assert up.reap_orphans() == []
