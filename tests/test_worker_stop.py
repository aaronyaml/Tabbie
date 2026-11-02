"""Agents block Stop (sidecar POST /worker/stop) and the settled block's files + resume (GET /request/files)."""
from __future__ import annotations
import importlib.util, json, os, subprocess, sys, textwrap, threading, time
from importlib import import_module
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "plugin" / "command-centre"
if "command_centre" not in sys.modules:
    spec = importlib.util.spec_from_file_location("command_centre", PKG / "__init__.py",
                                                  submodule_search_locations=[str(PKG)])
    _m = importlib.util.module_from_spec(spec); sys.modules["command_centre"] = _m
    spec.loader.exec_module(_m)
pv = import_module("command_centre.providers")

RID = "20261102-120000-abc123"
SID = "webui-sess-1"


def _sidecar():
    spec = importlib.util.spec_from_file_location("cc_sidecar_stop", PKG / "sidecar.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def _wire(sc, tmp_path, monkeypatch, rid=RID):
    reqs = tmp_path / "requests"
    (reqs / rid).mkdir(parents=True)
    monkeypatch.setattr(sc.common, "REQUESTS_DIR", reqs)
    monkeypatch.setattr(sc.common, "LOCKS_DIR", tmp_path / "locks")
    monkeypatch.setattr(sc, "_is_worker", lambda pid: True)  # the fake workers here are python, not claude/codex
    monkeypatch.setattr(sc.agents, "session_view", lambda sid: {"session_id": sid, "requests": [{"id": rid}] if sid == SID else []})
    return reqs / rid


def _gone(proc, secs=8.0):
    end = time.time() + secs
    while time.time() < end:
        if proc.poll() is not None:
            return True
        time.sleep(0.1)
    return False


def test_stop_rejects_bad_session_and_reports_nothing_running(tmp_path, monkeypatch):
    sc = _sidecar()
    _wire(sc, tmp_path, monkeypatch)
    assert sc.worker_stop({"session": "../etc"})[0] == 400
    assert sc.worker_stop({})[0] == 400
    assert sc.worker_stop({"session": SID}) == (200, {"ok": False, "reason": "nothing running"})


def test_stop_kills_only_this_sessions_live_worker(tmp_path, monkeypatch):
    sc = _sidecar()
    d = _wire(sc, tmp_path, monkeypatch)
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
    try:
        (d / "live.json").write_text(json.dumps({"open": True, "pid": proc.pid}), encoding="utf-8")
        # another chat can't stop it, and a pid in the body is ignored
        assert sc.worker_stop({"session": "other-chat", "pid": proc.pid})[1]["ok"] is False
        assert proc.poll() is None
        code, out = sc.worker_stop({"session": SID})
        assert code == 200 and out == {"ok": True, "request_id": RID}
        assert (d / "stopped_by_user").exists()
        assert _gone(proc)
    finally:
        if proc.poll() is None:
            proc.kill()


def test_stop_ignores_a_closed_channel(tmp_path, monkeypatch):
    sc = _sidecar()
    d = _wire(sc, tmp_path, monkeypatch)
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
    try:
        (d / "live.json").write_text(json.dumps({"open": False, "pid": proc.pid}), encoding="utf-8")
        assert sc.worker_stop({"session": SID})[1]["ok"] is False
        assert proc.poll() is None and not (d / "stopped_by_user").exists()
    finally:
        proc.kill()


def test_stop_reaches_a_running_codex_executor(tmp_path, monkeypatch):
    sc = _sidecar()
    d = _wire(sc, tmp_path, monkeypatch)
    (d / "request.json").write_text(json.dumps({"id": RID, "session_id": SID, "status": "executing"}), encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
    try:
        (tmp_path / "locks").mkdir()
        (tmp_path / "locks" / f"takeover-{SID}.pid").write_text(str(proc.pid), encoding="utf-8")
        assert sc.worker_stop({"session": SID})[1] == {"ok": True, "request_id": RID}
        assert _gone(proc) and (d / "stopped_by_user").exists()
    finally:
        if proc.poll() is None:
            proc.kill()


def test_http_route_needs_the_token(tmp_path, monkeypatch):
    sc = _sidecar()
    monkeypatch.setattr(sc, "TOKEN_FILE", tmp_path / "missing.token")
    from http.server import ThreadingHTTPServer
    import urllib.request, urllib.error
    srv = ThreadingHTTPServer(("127.0.0.1", 0), sc.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{srv.server_port}/worker/stop", data=b'{"session": "x"}',
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            urllib.request.urlopen(req, timeout=5)
            assert False, "expected 401"
        except urllib.error.HTTPError as e:
            assert e.code == 401
    finally:
        srv.shutdown()


# A Claude worker that starts (init event with its session id) and then works until it is killed.
SLOW = textwrap.dedent("""
    import json, sys, time
    sys.stdin.readline()
    print(json.dumps({"type": "system", "subtype": "init", "session_id": "sess-1234abcd"}), flush=True)
    time.sleep(60)
""")


class _NoSlot:
    def __init__(self, *a, **k): pass
    def __enter__(self): return self
    def __exit__(self, *e): return False


def test_run_claude_reports_stopped_by_you(tmp_path, monkeypatch):
    sc = _sidecar()
    d = _wire(sc, tmp_path, monkeypatch)
    (d / "stopped_by_user").write_text("{}", encoding="utf-8")  # left over from an earlier Stop: must not count
    fake = tmp_path / "slow.py"; fake.write_text(SLOW, encoding="utf-8")
    monkeypatch.setattr(pv, "claude_auth", lambda: (True, "subscription"))
    monkeypatch.setattr(pv, "build_claude_cmd", lambda *a, **k: [sys.executable, str(fake)])
    monkeypatch.setattr(pv, "WorkerSlot", _NoSlot)

    def stopper():
        end = time.time() + 15
        while time.time() < end:
            live = json.loads((d / "live.json").read_text(encoding="utf-8")) if (d / "live.json").exists() else {}
            if live.get("open") and not (d / "stopped_by_user").exists():
                time.sleep(0.5)
                sc.worker_stop({"session": SID})
                return
            time.sleep(0.1)
    threading.Thread(target=stopper, daemon=True).start()
    res = pv.run_claude("do it", cwd=str(tmp_path), add_dirs=[], cfg={"executor": {"timeout_seconds": 60}},
                        resume_session=None, on_progress=lambda _t: None, inbox=d)
    assert res["ok"] is False and res["error"] == "stopped by you" and res["stopped_by_user"]
    assert res["session_id"] == "sess-1234abcd"
    assert pv.usage_limit_info(res) is None


def test_codex_stop_wording(tmp_path):
    marker = tmp_path / "stopped_by_user"
    code = pv._STOP_CODES[0]  # -SIGTERM on macOS, taskkill's 1 on Windows
    assert not pv._user_stopped(tmp_path, code)
    marker.write_text("{}", encoding="utf-8")
    assert all(pv._user_stopped(tmp_path, c) for c in pv._STOP_CODES)
    assert not pv._user_stopped(tmp_path, 0) and not pv._user_stopped(None, -15)
    pv._clear_stop(tmp_path)
    assert not marker.exists()


def test_stopped_note_tells_the_hub_who_stopped_it():
    cc = sys.modules["command_centre"]
    assert cc._stopped_note({"ok": False}) == {}
    note = cc._stopped_note({"ok": False, "stopped_by_user": True})
    assert note["stopped"] == "stopped by you" and "Stop button" in note["note"]


# ---- GET /request/files ------------------------------------------------------------------------

def test_request_files_lists_edits_in_the_run_window_and_resume(tmp_path, monkeypatch):
    sc = _sidecar()
    d = _wire(sc, tmp_path, monkeypatch)
    from hmem import trace as hm_trace
    projects = tmp_path / "claude-projects"
    monkeypatch.setattr(hm_trace, "CLAUDE_PROJECTS", projects)
    cwd = str(tmp_path / "my proj")
    sess = "0d6f1c2a-1111-2222-3333-444455556666"
    (d / "request.json").write_text(json.dumps({"id": RID, "session_id": SID, "status": "done", "project": cwd,
                                                "executor": {"provider": "claude-cli", "session_id": sess}}), encoding="utf-8")
    t0 = 1_800_000_000
    (d / "agents.json").write_text(json.dumps({"agents": [{"id": "claude-1", "parent": None, "started": t0, "ended": t0 + 60}]}), encoding="utf-8")

    def ev(t, name, fp):
        return json.dumps({"type": "assistant", "timestamp": hm_trace._iso(t), "sessionId": sess, "cwd": cwd,
                           "message": {"content": [{"type": "tool_use", "name": name, "input": {"file_path": fp}}]}})
    tp = hm_trace.transcript_path(cwd, sess)
    tp.parent.mkdir(parents=True)
    tp.write_text("\n".join([ev(t0 - 500, "Edit", "/x/earlier-run.py"),  # an earlier request in the same session
                             ev(t0 + 5, "Read", "/x/read-only.py"),
                             ev(t0 + 10, "Edit", "/x/app.py"), ev(t0 + 20, "Write", "/x/new.md"),
                             ev(t0 + 30, "Edit", "/x/app.py")]) + "\n", encoding="utf-8")
    out = sc.request_files(RID)
    assert out["files"] == ["/x/app.py", "/x/new.md"] and out["total"] == 2
    assert out["resume"] == sess
    assert out["resume_cmd"] == f"cd '{cwd}' && claude --resume {sess}"


def test_request_files_running_codex_and_bad_session(tmp_path, monkeypatch):
    sc = _sidecar()
    d = _wire(sc, tmp_path, monkeypatch)
    (d / "request.json").write_text(json.dumps({"id": RID, "status": "executing"}), encoding="utf-8")
    assert sc.request_files(RID)["running"] is True
    (d / "request.json").write_text(json.dumps({"id": RID, "status": "done",
                                                "executor": {"provider": "codex-cli", "thread_id": "t1"}}), encoding="utf-8")
    out = sc.request_files(RID)
    assert out["files"] == [] and out["resume_cmd"] is None
    (d / "request.json").write_text(json.dumps({"id": RID, "status": "done",
                                                "executor": {"provider": "claude-cli", "session_id": "x; rm -rf ~"}}), encoding="utf-8")
    assert sc.request_files(RID)["resume_cmd"] is None


def test_retry_claim_once_for_a_recent_interrupted_run(tmp_path, monkeypatch):
    sc = _sidecar()
    d = _wire(sc, tmp_path, monkeypatch)
    monkeypatch.setattr(sc.agents, "AGENTS_INDEX_DIR", tmp_path / "agents")
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / f"{sc.agents._safe(SID)}.json").write_text(json.dumps({"requests": {RID: {}}}), encoding="utf-8")
    req = {"id": RID, "status": "failed", "result": {"ok": False, "error": sc.updates.INTERRUPTED}}
    (d / "request.json").write_text(json.dumps(req), encoding="utf-8")
    assert sc.retry_claim({"session": SID})[1] == {"ok": True, "request_id": RID}
    assert sc.retry_claim({"session": SID})[1] == {"ok": False}            # once only
    (d / "request.json").write_text(json.dumps({**req, "result": {"ok": False, "error": "boom"}}), encoding="utf-8")
    assert sc.retry_claim({"session": SID})[1] == {"ok": False}            # only restart cut-offs
    (d / "request.json").write_text(json.dumps(req), encoding="utf-8")
    old = time.time() - 3 * 3600
    os.utime(d / "request.json", (old, old))
    assert sc.retry_claim({"session": SID})[1] == {"ok": False}            # too old
    assert sc.retry_claim({"session": "../x"})[0] == 400


def test_token_totals_add_up_runs_by_day_and_model(tmp_path, monkeypatch):
    import datetime as dt
    sc = _sidecar()
    reqs = tmp_path / "requests"
    monkeypatch.setattr(sc.common, "REQUESTS_DIR", reqs)
    today = dt.date.today().strftime("%Y%m%d")
    for i, (model, usage) in enumerate([("claude-opus-5-5", {"input": 1000, "cached": 800, "output": 50}),
                                        ("gpt-6-luna", {"input": 300, "cached": 0, "output": 20}),
                                        ("claude-opus-5-5", None)]):
        d = reqs / f"{today}-12000{i}-abcde{i}"; d.mkdir(parents=True)
        d.joinpath("request.json").write_text(json.dumps({"executor": {"provider": "claude-cli", "model": model, "usage": usage}}), encoding="utf-8")
    (reqs / "20200101-000000-aaaaaa").mkdir()
    out = sc.token_totals("7")
    assert out["total"] == {"runs": 2, "input": 1300, "cached": 800, "output": 70, "unknown": 1}
    assert list(out["by_model"]) == ["claude-opus-5-5", "gpt-6-luna"]
    assert out["by_model"]["claude-opus-5-5"]["runs"] == 1


def test_inbox_lists_chats_waiting_on_the_user(tmp_path, monkeypatch):
    import datetime as dt
    sc = _sidecar()
    reqs = tmp_path / "requests"
    monkeypatch.setattr(sc.common, "REQUESTS_DIR", reqs)
    monkeypatch.setattr(sc, "WEBUI_STATE", tmp_path / "webui")
    (tmp_path / "webui" / "sessions").mkdir(parents=True)
    today = dt.date.today().strftime("%Y%m%d")
    def req(n, sid, status, q=None):
        d = reqs / f"{today}-12000{n}-abcde{n}"; d.mkdir(parents=True)
        d.joinpath("request.json").write_text(json.dumps({"session_id": sid, "status": status, "question": q}), encoding="utf-8")
    def sess(sid, **kw):
        (tmp_path / "webui" / "sessions" / f"{sid}.json").write_text(json.dumps({"title": sid.upper(), **kw}), encoding="utf-8")
    sess("aaa111"); sess("bbb222"); sess("ccc333", archived=True); sess("ddd444")
    req(1, "aaa111", "awaiting_answer", {"questions": [{"question": "Which colour?"}]})
    req(2, "bbb222", "awaiting_approval")
    req(3, "ccc333", "awaiting_answer")          # archived chat
    req(4, "ddd444", "awaiting_answer"); req(5, "ddd444", "done")  # answered since
    req(6, "eee555", "awaiting_answer")          # chat deleted
    out = sc.inbox()
    assert [(i["session"], i["kind"]) for i in out["items"]] == [("bbb222", "plan"), ("aaa111", "question")]
    assert out["items"][1]["text"] == "Which colour?"
