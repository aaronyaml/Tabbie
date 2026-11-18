"""Team for more than one person: a changed team key reaches every teammate and their own devices, people
can be added (account + passkey link from the inviting Mac) and removed (new key they don't get)."""
import importlib
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("cryptography")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "team"))


@pytest.fixture
def tt(tmp_path, monkeypatch):
    monkeypatch.setenv("TT_HOME", str(tmp_path))
    import tt as mod
    mod = importlib.reload(mod)
    mod.ensure_mac_keys("Leo's Mac")
    monkeypatch.setattr(mod, "token", lambda force=False: "x")
    mod.RELAY = "https://relay.test"
    return mod


def _device(C):
    sk, xk = C.new_key(), C.new_key()
    sp = C.pub_raw(sk.public_key())
    return {"kid": C.kid_of(sp), "sign_pub": sp, "ecdh_pub": C.pub_raw(xk.public_key()), "sk": sk, "xk": xk}


def test_new_key_from_a_teammate_reaches_my_own_devices(tt, monkeypatch):
    C = tt.C
    aaron, phone = _device(C), _device(C)
    me = tt.load("mac.json")
    tt.save("trust.json", {"owner": "leo", "macs": {"aaron": {"kid": aaron["kid"], "sign_pub": aaron["sign_pub"]}},
                           "allow": {phone["kid"]: {"sign_pub": phone["sign_pub"], "ecdh_pub": phone["ecdh_pub"], "name": "Phone"}}})
    tt.save("team.json", {"current": "kold", "keys": {"kold": C.b64u(os.urandom(32))}})
    key = os.urandom(32)
    w = C.wrap(key, "knew", me["kid"], me["ecdh_pub"], aaron["kid"], aaron["sk"])
    w.update(wrapper_kid=aaron["kid"], created_at=2)
    posted = []

    def api(method, path, body=None):
        if path == "/api/wraps" and method == "GET":
            return {"wraps": [w]}
        if path == "/api/wraps" and method == "POST":
            posted.extend(body["wraps"])
            return {"ok": True}
        raise AssertionError(path)
    monkeypatch.setattr(tt, "api", api)
    assert tt.sync_keys() == ["knew"]
    assert tt.team()["current"] == "knew"
    # the phone can open it, signed by this (Leo's) Mac, the only Mac it trusts
    pw = next(p for p in posted if p["recipient_kid"] == phone["kid"])
    pw["wrapper_kid"] = me["kid"]
    assert C.unwrap(pw, phone["kid"], phone["xk"], me["sign_pub"]) == key
    posted.clear()
    assert tt.sync_keys() == [] and posted == []  # nothing new: nothing sent


def test_wraps_from_unknown_macs_are_ignored(tt, monkeypatch):
    C = tt.C
    stranger = _device(C)
    me = tt.load("mac.json")
    tt.save("trust.json", {"owner": "leo", "macs": {}, "allow": {}})
    w = C.wrap(os.urandom(32), "kx", me["kid"], me["ecdh_pub"], stranger["kid"], stranger["sk"])
    w["wrapper_kid"] = stranger["kid"]
    monkeypatch.setattr(tt, "api", lambda m, p, b=None: {"wraps": [w]})
    assert tt.sync_keys() == [] and "kx" not in tt.team()["keys"]


def test_invite_makes_the_account_and_a_passkey_link(tt, monkeypatch):
    tt.save("trust.json", {"owner": "aaron", "macs": {}, "allow": {}})
    tt.save("team.json", {"current": "k1", "keys": {"k1": tt.C.b64u(os.urandom(32))}})
    calls = []

    def api(method, path, body=None):
        calls.append(path)
        if path == "/api/users":
            assert body["name"] == "sam" and len(body["token_hash"]) == 43
            return {"name": "sam", "created": True, "enrol": True}
        if path == "/api/macs/invites":
            return {"for": "sam", "expires_at": 1}
        raise AssertionError(path)
    monkeypatch.setattr(tt, "api", api)
    r = tt.invite("Sam")
    assert calls == ["/api/users", "/api/macs/invites"]
    assert r["enrol"].startswith("https://relay.test/enrol#") and r["code"]


def test_existing_account_gets_no_link(tt, monkeypatch):
    monkeypatch.setattr(tt, "api", lambda m, p, b=None: {"name": "leo", "created": False, "enrol": False})
    assert tt.add_account("leo") == ""


def test_remove_teammate_rotates_without_them(tt, monkeypatch):
    C = tt.C
    leo, phone = _device(C), _device(C)
    tt.save("trust.json", {"owner": "aaron", "macs": {"leo": {"kid": leo["kid"], "sign_pub": leo["sign_pub"]}},
                           "allow": {phone["kid"]: {"sign_pub": phone["sign_pub"], "ecdh_pub": phone["ecdh_pub"], "name": "Phone"}}})
    tt.save("team.json", {"current": "k1", "keys": {"k1": C.b64u(os.urandom(32))}})
    posted, removed = [], []

    def api(method, path, body=None):
        if path == "/api/users/leo/remove":
            removed.append(1)
            return {"ok": True}
        if path == "/api/directory":  # the relay now lists Leo's Mac as revoked
            return {"devices": [{"kid": leo["kid"], "status": "revoked", "sign_pub": leo["sign_pub"], "ecdh_pub": leo["ecdh_pub"]}]}
        if path == "/api/wraps":
            posted.extend(body["wraps"])
            return {"ok": True}
        raise AssertionError(path)
    monkeypatch.setattr(tt, "api", api)
    assert "Removed leo" in tt.remove_teammate("leo")
    assert removed and tt.team()["current"] != "k1"
    assert {w["recipient_kid"] for w in posted} == {phone["kid"]}  # not Leo's Mac
    assert "leo" not in tt.load("trust.json")["macs"]
    with pytest.raises(tt.Fail):
        tt.remove_teammate("aaron")


def test_a_failed_hook_is_retried_until_it_works(tt, tmp_path, monkeypatch):
    flag = tmp_path / "ok"
    script = tmp_path / "hook.sh"
    script.write_text(f'#!/bin/sh\n[ -f "{flag}" ] || {{ echo "service down" >&2; exit 1; }}\necho "$@" >> "{tmp_path}/ran"\n')
    script.chmod(0o755)
    cfg = tt.config()
    cfg["hooks"] = {"trusted": [str(script)]}
    tt.save("config.json", cfg)
    monkeypatch.setattr(tt.threading if hasattr(tt, "threading") else __import__("threading"), "Thread",
                        lambda *a, **k: type("T", (), {"start": lambda self: None})())
    tt.run_hook("trusted", "sam", "kid1", "pub1")
    assert tt.run_pending_hooks(now=1000) == []
    p = tt.load("hooks.json")["pending"][0]
    assert p["tries"] == 1 and "service down" in p["error"] and p["next"] == 1060
    assert tt.run_pending_hooks(now=1030) == []  # not due yet
    flag.write_text("")
    assert tt.run_pending_hooks(now=1061) == ["trusted"]
    assert tt.load("hooks.json")["pending"] == [] and (tmp_path / "ran").read_text().strip() == "sam kid1 pub1"


def test_a_wrong_clock_is_noticed_from_the_relays_date(tt):
    from email.utils import formatdate
    import time as _t
    tt._note_clock(formatdate(_t.time() - 600, usegmt=True))  # this Mac is 10 minutes fast
    w = tt.clock_warning()
    assert w and "10 minutes fast" in w and "Date & Time" in w
    tt._note_clock(formatdate(_t.time(), usegmt=True))
    assert tt.clock_warning() is None
    tt._note_clock("not a date")
    assert tt.clock_warning() is None
