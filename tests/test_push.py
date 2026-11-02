"""Web Push: only real push-service subscriptions are kept, and the watcher queues one event (and one
empty push) per job that finishes or needs the user; old requests seen on start never push."""
from __future__ import annotations
import importlib.util, json, sys
from importlib import import_module
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "plugin" / "command-centre"
if "command_centre" not in sys.modules:
    spec = importlib.util.spec_from_file_location("command_centre", PKG / "__init__.py",
                                                  submodule_search_locations=[str(PKG)])
    _m = importlib.util.module_from_spec(spec); sys.modules["command_centre"] = _m
    spec.loader.exec_module(_m)
push = import_module("command_centre.push")
common = import_module("command_centre.common")


def _wire(monkeypatch, tmp_path):
    monkeypatch.setattr(push, "DIR", tmp_path / "push")
    monkeypatch.setattr(push, "SUBS", tmp_path / "push" / "subs.json")
    monkeypatch.setattr(push, "LATEST", tmp_path / "push" / "latest.json")
    monkeypatch.setattr(common, "REQUESTS_DIR", tmp_path / "req")
    (tmp_path / "web" / "sessions").mkdir(parents=True)


def _sub(ep):
    return {"endpoint": ep, "keys": {"p256dh": "BXYZ", "auth": "abc"}}


def test_only_real_push_services_can_subscribe(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path)
    assert push.subscribe({"subscription": _sub("https://web.push.apple.com/QGx1")})[0] == 200
    assert push.subscribe({"subscription": _sub("https://fcm.googleapis.com/fcm/send/x")})[0] == 200
    for bad in ("http://web.push.apple.com/x", "https://evil.example/x", "https://127.0.0.1/x",
                "https://push.apple.com.evil.example/x"):
        assert push.subscribe({"subscription": _sub(bad)})[0] == 400, bad
    assert push.subscribe({"subscription": {"endpoint": "https://web.push.apple.com/y"}})[0] == 400  # no keys
    assert len(push._subs()) == 2
    push.unsubscribe({"endpoint": "https://web.push.apple.com/QGx1"})
    assert [s["endpoint"] for s in push._subs()] == ["https://fcm.googleapis.com/fcm/send/x"]


def test_watcher_pushes_once_per_finished_or_waiting_job(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path)
    web = tmp_path / "web"
    (web / "sessions" / "aaaaaaaaaaaa.json").write_text(json.dumps({"title": "Fix the login"}))
    rd = tmp_path / "req"

    def req(rid, **data):
        (rd / rid).mkdir(parents=True, exist_ok=True)
        (rd / rid / "request.json").write_text(json.dumps({"session_id": "aaaaaaaaaaaa", **data}))
    req("20261102-100000-aaaaaa", status="done", result={"result": "old news"})
    sent = []
    w = push.Watcher(web, send=lambda: sent.append(1))
    push.subscribe({"subscription": _sub("https://web.push.apple.com/z")})
    assert w.tick() == [] and not sent  # baseline: nothing on start
    req("20261102-110000-bbbbbb", status="executing")
    assert w.tick() == []
    req("20261102-110000-bbbbbb", status="done", result={"result": "Route: Claude\n\nThe login works again.\nMore."})
    ev = w.tick()
    assert [(e["title"], e["body"], e["url"]) for e in ev] == [("Fix the login: done", "The login works again.", "/session/aaaaaaaaaaaa")]
    assert sent == [1] and w.tick() == []  # once
    req("20261102-120000-cccccc", status="awaiting_answer", question={"questions": [{"question": "Which colour?"}]})
    assert [e["title"] for e in w.tick()] == ["Fix the login: needs your answer"]
    assert [e["body"] for e in push.latest()] == ["Which colour?", "The login works again."]
