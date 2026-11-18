"""Signing in with the team site's link must never start a second team key when the team already exists:
that Mac waits for an invite code from a teammate instead, and the invite then works on it."""
import importlib
import sys
from pathlib import Path

import pytest

pytest.importorskip("cryptography")  # bin/tt brings it via uv; the plain test run may not have it
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "team"))


@pytest.fixture
def tt(tmp_path, monkeypatch):
    monkeypatch.setenv("TT_HOME", str(tmp_path))
    import tt as mod
    mod = importlib.reload(mod)
    mod.ensure_mac_keys("Test Mac")
    mod.save("token.json", {"user": "leo", "token": "x", "exp": 0})
    monkeypatch.setattr(mod, "token", lambda force=False: "x")
    monkeypatch.setattr(mod, "receive_wraps", lambda: 0)
    return mod


def directory(mod, monkeypatch, devices):
    monkeypatch.setattr(mod, "api", lambda method, path, body=None: {"devices": devices} if path == "/api/directory" else {})


def test_existing_team_needs_an_invite_not_a_new_key(tt, monkeypatch):
    directory(tt, monkeypatch, [{"owner": "aaron", "kind": "mac", "status": "approved"},
                                {"owner": "leo", "kind": "mac", "status": "approved"}])
    r = tt.pair_finish()
    assert r["state"] == "needs_invite" and r["teammates"] == ["aaron"]
    assert not tt.team()["current"]
    d = tt.info()
    assert d["linked"] is False and d["pairing"]["state"] == "needs_invite"


def test_first_mac_on_a_new_team_makes_the_key(tt, monkeypatch):
    directory(tt, monkeypatch, [{"owner": "aaron", "kind": "phone", "status": "approved"}])
    r = tt.pair_finish()
    assert r["state"] == "linked" and r["new_team_key"] and tt.team()["current"]


def test_key_arriving_later_finishes_linking(tt, monkeypatch):
    directory(tt, monkeypatch, [{"owner": "aaron", "kind": "mac", "status": "approved"}])
    tt.pair_finish()
    def wraps():
        t = tt.team(); t["keys"]["k1"] = "a" * 43; t["current"] = "k1"; tt.save("team.json", t); return 1
    monkeypatch.setattr(tt, "receive_wraps", wraps)
    assert tt.pair_check()["state"] == "linked"
    assert tt.load("needkey.json") is None
