"""Protocol core: forged, replayed, stale, tampered and untrusted events are rejected."""
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("cryptography")  # bin/tt brings it via uv; the plain test run may not have it
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "team"))
import ttcore as C  # noqa: E402

ROOM = "lobby"


def make(owner="aaron"):
    sk, xk = C.new_key(), C.new_key()
    sp = C.pub_raw(sk.public_key())
    return {"sk": sk, "xk": xk, "sign_pub": sp, "ecdh_pub": C.pub_raw(xk.public_key()), "kid": C.kid_of(sp), "owner": owner}


@pytest.fixture
def world():
    mac, phone, stranger = make(), make(), make()
    leo_mac, leo_phone = make("leo"), make("leo")
    team = {"k1": os.urandom(32)}
    trust = {"owner": "aaron", "mac": {"kid": mac["kid"], "sign_pub": mac["sign_pub"]},
             "allow": {phone["kid"]: {"sign_pub": phone["sign_pub"], "name": "iPhone"}},
             "macs": {"leo": {"kid": leo_mac["kid"], "sign_pub": leo_mac["sign_pub"]}}}
    issued = 1
    cert = C.sign(leo_mac["sk"], C.cert_parts(leo_phone["kid"], "leo", "browser", "Leo iPhone", leo_phone["sign_pub"], leo_phone["ecdh_pub"], issued))
    directory = [{"kid": leo_phone["kid"], "owner": "leo", "kind": "browser", "name": "Leo iPhone", "sign_pub": leo_phone["sign_pub"],
                  "ecdh_pub": leo_phone["ecdh_pub"], "issued_at": issued, "cert_sig": cert, "revoked_at": None, "revoke_sig": None, "status": "approved"}]
    v = C.Verifier(trust, team)
    v.load_directory(directory)
    return dict(mac=mac, phone=phone, stranger=stranger, leo_mac=leo_mac, leo_phone=leo_phone, team=team, v=v, directory=directory)


def ev(dev, team, type_="prompt", author=None, **kw):
    return C.seal(ROOM, type_, author or dev["owner"], dev["kid"], dev["sk"], "k1", team["k1"], {"text": "hi"}, **kw)


def test_owner_prompt_runs(world):
    out = world["v"].open(ev(world["phone"], world["team"]), ROOM, 1)
    assert out["payload"] == {"text": "hi"} and out["can_execute"]


def test_same_event_redelivered_shows_but_never_runs_twice(world):
    e = ev(world["phone"], world["team"])
    assert world["v"].open(e, ROOM, 1)["can_execute"]
    assert not world["v"].open(e, ROOM, 1)["can_execute"]


def test_replay_under_new_seq_rejected(world):
    e = ev(world["phone"], world["team"])
    world["v"].open(e, ROOM, 1)
    with pytest.raises(C.Rejected, match="replay"):
        world["v"].open(e, ROOM, 2)


def test_forged_signature_rejected(world):
    e = ev(world["stranger"], world["team"], author="aaron")
    e["kid"] = world["phone"]["kid"]  # claims to be the phone, signed by someone else
    with pytest.raises(C.Rejected, match="bad signature"):
        world["v"].open(e, ROOM, 1)


def test_unknown_device_rejected(world):
    with pytest.raises(C.Rejected, match="untrusted"):
        world["v"].open(ev(world["stranger"], world["team"], author="aaron"), ROOM, 1)


def test_tampered_ciphertext_rejected(world):
    e = ev(world["phone"], world["team"])
    e["ct"] = e["ct"][:-2] + ("AA" if e["ct"][-2:] != "AA" else "BB")
    with pytest.raises(C.Rejected):
        world["v"].open(e, ROOM, 1)


def test_relay_cannot_change_type_or_room(world):
    e = ev(world["phone"], world["team"], type_="status")
    e2 = dict(e, type="prompt")
    with pytest.raises(C.Rejected, match="bad signature"):
        world["v"].open(e2, ROOM, 1)
    with pytest.raises(C.Rejected, match="wrong room"):
        world["v"].open(dict(e, room="other"), ROOM, 1)


def test_stale_prompt_rejected(world):
    e = ev(world["phone"], world["team"], ts=C.now_ms() - 10 * 60_000)
    with pytest.raises(C.Rejected, match="stale"):
        world["v"].open(e, ROOM, 1)


def test_other_persons_prompt_shown_but_never_run(world):
    out = world["v"].open(ev(world["leo_phone"], world["team"]), ROOM, 1)
    assert out["payload"] == {"text": "hi"} and not out["can_execute"]


def test_other_persons_device_needs_their_macs_cert(world):
    d = dict(world["directory"][0], name="Evil")  # relay edits the cert's content
    v = C.Verifier(world["v"].trust, world["team"])
    v.load_directory([d])
    with pytest.raises(C.Rejected, match="untrusted"):
        v.open(ev(world["leo_phone"], world["team"]), ROOM, 1)


def test_revoked_device_rejected(world):
    d = dict(world["directory"][0])
    d["revoked_at"] = 5
    d["revoke_sig"] = C.sign(world["leo_mac"]["sk"], C.revoke_parts(d["kid"], "leo", 5))
    v = C.Verifier(world["v"].trust, world["team"])
    v.load_directory([d])
    with pytest.raises(C.Rejected, match="untrusted"):
        v.open(ev(world["leo_phone"], world["team"]), ROOM, 1)


def test_unknown_team_key_rejected(world):
    e = C.seal(ROOM, "status", "aaron", world["phone"]["kid"], world["phone"]["sk"], "k2", os.urandom(32), {"t": 1})
    with pytest.raises(C.Rejected, match="team key"):
        world["v"].open(e, ROOM, 1)


def test_wrap_roundtrip_and_forged_wrapper(world):
    mac, phone = world["mac"], world["phone"]
    w = C.wrap(world["team"]["k1"], "k1", phone["kid"], phone["ecdh_pub"], mac["kid"], mac["sk"])
    w["wrapper_kid"] = mac["kid"]
    assert C.unwrap(w, phone["kid"], phone["xk"], mac["sign_pub"]) == world["team"]["k1"]
    with pytest.raises(ValueError):
        C.unwrap(w, phone["kid"], phone["xk"], world["stranger"]["sign_pub"])


def test_fingerprint_format():
    fp = C.fingerprint(make()["sign_pub"])
    assert len(fp) == 14 and fp[4] == fp[9] == "-"


def test_removed_device_history_shown_new_events_rejected(world):
    t = world["v"].trust
    phone = world["phone"]
    t["removed"] = {phone["kid"]: dict(t["allow"].pop(phone["kid"]), removed_at=C.now_ms())}
    old = ev(phone, world["team"], type_="status", ts=C.now_ms() - 60_000)
    out = world["v"].open(old, ROOM, 1)
    assert "(removed)" in out["device"] and not out["can_execute"]
    with pytest.raises(C.Rejected, match="removed"):
        world["v"].open(ev(phone, world["team"], ts=C.now_ms() + 1000), ROOM, 2)


# ---------- relay check (/.well-known/tabbie-team) ----------
import io  # noqa: E402

import tt  # noqa: E402


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_relay_check(monkeypatch):
    seen = []

    def urlopen(req, timeout=10):
        seen.append(req.full_url)
        return _Resp(b'{"protocol": "tt1", "version": 1, "name": "Demo\\u0007 team"}')

    monkeypatch.setattr(tt.urllib.request, "urlopen", urlopen)
    assert tt.check_relay("team.example.com/") == {"relay": "https://team.example.com", "name": "Demo team"}
    assert seen == ["https://team.example.com/.well-known/tabbie-team"]
    for bad in ("http://team.example.com", "https://team.example.com/x", "https://u@team.example.com"):
        with pytest.raises(tt.Fail):
            tt.check_relay(bad)


def test_relay_check_wrong_protocol(monkeypatch):
    monkeypatch.setattr(tt.urllib.request, "urlopen", lambda req, timeout=10: _Resp(b'{"protocol": "tt9"}'))
    with pytest.raises(tt.Fail, match="isn't a Tabbie team relay"):
        tt.check_relay("https://team.example.com")


# ---------- invites ----------
def test_invite_roundtrip_and_proof():
    secret = os.urandom(32)
    code = C.make_invite("https://team.example.com", "leo", secret, {"owner": "aaron", "kid": "A" * 22})
    assert code.startswith("TT1-") and len(code) < 260
    inv = C.read_invite("  " + code[:40] + "\n" + code[40:] + " ")  # pasted with line breaks / spaces
    assert inv["relay"] == "https://team.example.com" and inv["for"] == "leo" and inv["inviter"] == {"owner": "aaron", "kid": "A" * 22}
    assert inv["secret"] == secret and inv["token"] == C.invite_token(secret) and inv["token"] != C.b64u(secret)
    k, x = C.new_key(), C.new_key()
    sp, xp = C.pub_raw(k.public_key()), C.pub_raw(x.public_key())
    proof = C.invite_proof(secret, sp, xp)
    assert C.proof_ok(secret, sp, xp, proof)
    assert not C.proof_ok(os.urandom(32), sp, xp, proof)          # wrong secret
    assert not C.proof_ok(secret, C.pub_raw(C.new_key().public_key()), xp, proof)  # other keys
    assert not C.proof_ok(secret, sp, xp, None)


def test_read_invite_rejects_junk():
    import pytest
    for bad in ["", "hello", "TT1-", "TT1-!!!", "TT1-" + C.b64u(b'{"r":"x"}'),
                C.make_invite("https://t", "leo", os.urandom(16), {"owner": "a", "kid": "A" * 22})]:
        with pytest.raises(ValueError):
            C.read_invite(bad)
