"""Reliability: Stop never signals a reused pid, the reaper settles stuck runs, a crashed run ends as failed."""
from __future__ import annotations
import importlib.util, json, os, subprocess, sys, time
from importlib import import_module
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "plugin" / "command-centre"
if "command_centre" not in sys.modules:
    spec = importlib.util.spec_from_file_location("command_centre", PKG / "__init__.py",
                                                  submodule_search_locations=[str(PKG)])
    _m = importlib.util.module_from_spec(spec); sys.modules["command_centre"] = _m
    spec.loader.exec_module(_m)
cc = sys.modules["command_centre"]
up = import_module("command_centre.updates")


def _sidecar():
    spec = importlib.util.spec_from_file_location("cc_sidecar_rel", PKG / "sidecar.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def test_is_worker_rejects_unrelated_and_dead_pids():
    sc = _sidecar()
    p = subprocess.Popen(["sleep", "30"])
    try:
        assert not sc._is_worker(p.pid)          # alive, but not claude/codex
    finally:
        p.kill(); p.wait()
    assert not sc._is_worker(p.pid)              # gone
    assert not sc._is_worker(os.getpid())        # python


def test_reaper_skips_stray_files_and_closes_live(tmp_path, monkeypatch):
    rd = tmp_path / "requests"; d = rd / "20261102-000000-aaaaaa"; d.mkdir(parents=True)
    (rd / ".DS_Store").write_text("x")
    (d / "request.json").write_text(json.dumps({"status": "executing"}))
    (d / "live.json").write_text(json.dumps({"open": True, "pid": 12345}))
    old = time.time() - 600
    for f in d.iterdir():
        os.utime(f, (old, old))
    monkeypatch.setattr(up, "STATE_DIR", tmp_path)
    monkeypatch.setattr(up, "_workers_alive", lambda: False)
    assert up.reap_orphans() == [d.name]
    assert json.loads((d / "request.json").read_text())["status"] == "failed"
    assert json.loads((d / "live.json").read_text()) == {"open": False, "pid": None, "at": json.loads((d / "live.json").read_text())["at"]}


def test_crash_inside_a_run_ends_it_as_failed(monkeypatch, tmp_path):
    st = import_module("command_centre.state")
    ag = import_module("command_centre.agents")
    for mod, name in ((st, "REQUESTS_DIR"), (cc, "REQUESTS_DIR"), (ag, "REQUESTS_DIR")):
        monkeypatch.setattr(mod, name, tmp_path / "req")
    monkeypatch.setattr(st, "SESSIONS_DIR", tmp_path / "ses")
    monkeypatch.setattr(ag, "AGENTS_INDEX_DIR", tmp_path / "agents")
    monkeypatch.setattr(cc, "log_decision", lambda **k: None)

    def boom(*a, **k):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(cc, "_run_request", boom)
    r = st.Request.create(session_id="s", route="claude", reason="t", forced=False, prompt="x",
                          raw_message="x", project=None, project_source="t")
    r["status"] = "executing"; r.save()
    out = cc._execute(r)
    assert out["ok"] is False and "internal error" in out["error"]
    assert st.Request(r.id)["status"] == "failed"


def test_attachments_never_pick_up_keys_or_tabbie_state():
    at = import_module("command_centre.attachments")
    assert at._is_secret("/u/x/.ssh/config") and at._is_secret("/u/proj/server.pem")
    assert at._is_secret("/u/proj/id_ed25519") and at._is_secret("/u/.hermes/webui/sidecar-auth/sidecar.token")
    assert at._is_secret("/u/.hermes/webui/settings.json")
    assert not at._is_secret("/u/.hermes/webui/attachments/abc/photo.png")
    assert not at._is_secret("/u/proj/README.md") and not at._is_secret("/u/proj/keys.ts")
