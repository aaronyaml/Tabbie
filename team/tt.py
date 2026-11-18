"""tt: Tabbie Team, the Mac side. Connects this Tabbie to a team relay (any server speaking protocol tt1) so
shared sessions show live on your team's devices. Set up from Settings → Team, or here:

    tt connect URL                     check URL is a team relay (/.well-known/tabbie-team) and use it

    tt pair [--name NAME]              link this Mac to your account (enter the code on the relay's site)
    tt status                          who this Mac is, approved devices, team keys
    tt approve CODE                    approve a browser: type the code that browser shows
    tt revoke CODE                     remove one of your devices, then rotate the team key
    tt rotate                          new team key for every trusted device
    tt trust OWNER CODE                pin another person's Mac by its fingerprint and share the team key
    tt mail list [inbox|sent|archived|all] [--q WORDS]      your mailbox (this Mac's own; see mailroom.py)
    tt mail read ID                    the whole thread (marks it read)
    tt mail send --to A[,B] [--cc C] --subject S (--text T | --file F) [--reply ID]
    tt mail draft ID|- "what to say"   a draft in the house standard (- for a new email); nothing is sent
    tt mail triage                     note, draft and suggest follow-ups now (normally 16:00 UK time daily)
    tt invite NAME                     one code for NAME's Tabbie: links their Mac and trusts both ways
                                       (makes NAME's team account and a passkey link if they're new)
    tt remove NAME                     remove someone from the team: revokes their devices, new team key
    tt join CODE                       use an invite code (connects, links this Mac, trusts the inviter)
    tt listen [ROOM]                   show a room live (verified + decrypted here)
    tt send ROOM TEXT                  send an encrypted, signed test message
    tt share [PROJECT] / unshare P     which Tabbie projects are shared live (none until you pick)
    tt daemon                          mirror shared sessions to the relay (the LaunchAgent runs this)

Keys and settings live in ~/.hermes/team (0600 files), never in the relay or in git. TT_RELAY / TT_HOME
override them for tests. Protocol: docs/team/PROTOCOL.md.
"""
from __future__ import annotations

import asyncio
import json
import os
import platform
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ttcore as C  # noqa: E402

HOME = Path(os.environ.get("TT_HOME", Path.home() / ".hermes" / "team"))


def _relay() -> str:
    if os.environ.get("TT_RELAY"):
        return os.environ["TT_RELAY"].rstrip("/")
    try:
        return str(json.loads((HOME / "config.json").read_text()).get("relay") or "").rstrip("/")
    except (OSError, ValueError):
        return ""


RELAY = _relay()


class Fail(Exception):
    pass


# ---------- local state ----------
def load(name: str, default=None):
    p = HOME / name
    return json.loads(p.read_text()) if p.exists() else default


def save(name: str, data):
    HOME.mkdir(parents=True, exist_ok=True)
    os.chmod(HOME, 0o700)
    p = HOME / name
    tmp = p.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, p)


def config() -> dict:
    c = load("config.json", {}) or {}
    c.setdefault("projects", [])
    c.setdefault("sessions", [])
    return c


def mac_keys():
    m = load("mac.json")
    if not m:
        raise Fail("This Mac isn't linked yet. Run: tt pair")
    return m, C.load_key(m["sign"]), C.load_key(m["ecdh"])


def team() -> dict:
    return load("team.json", {"current": None, "keys": {}})


def team_bytes() -> dict[str, bytes]:
    keys = {k: C.unb64u(v) for k, v in team()["keys"].items()}
    me = load("me.json")
    if me:
        keys[me["key_id"]] = C.unb64u(me["key"])  # opens your own devices' mail requests; never given to anyone else
    return keys


def me_key() -> dict:
    """Your personal key (key ids start with "p"): only your own approved devices get it, never another person's
    Mac or devices. Private things (your mailbox) are sealed with it instead of the team key."""
    me = load("me.json")
    if not me:
        me = {"key_id": "p" + C.b64u(os.urandom(9)), "key": C.b64u(os.urandom(32)), "shared": []}
        save("me.json", me)
    return me


def share_me_key() -> int:
    """Wrap the personal key to each of your approved devices that doesn't have it yet."""
    me, trust = me_key(), load("trust.json") or {}
    todo = [(k, d["ecdh_pub"]) for k, d in trust.get("allow", {}).items() if k not in me["shared"]]
    if not todo:
        return 0
    m, sk, _ = mac_keys()
    wraps = [C.wrap(C.unb64u(me["key"]), me["key_id"], kid, ecdh, m["kid"], sk) for kid, ecdh in todo]
    api("POST", "/api/wraps", {"wraps": wraps})
    me["shared"] += [k for k, _ in todo]
    save("me.json", me)
    return len(todo)


# ---------- relay ----------
def http(method: str, path: str, body=None, token: str | None = None):
    if not RELAY:
        raise Fail("No team relay set yet. Open Settings → Team, or run: tt connect URL")
    req = urllib.request.Request(RELAY + path, method=method, data=None if body is None else json.dumps(body).encode())
    req.add_header("user-agent", "tabbie-team/1")
    if body is not None:
        req.add_header("content-type", "application/json")
    if token:
        req.add_header("authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def token(force=False) -> str:
    t = load("token.json")
    if t and not force and t["expires_at"] - C.now_ms() > 60_000:
        return t["token"]
    m, sk, _ = mac_keys()
    st, ch = http("POST", "/api/mac/challenge", {"kid": m["kid"]})
    if st != 200:
        raise Fail(ch.get("error") or f"relay said {st}")
    st, r = http("POST", "/api/mac/token", {"challengeId": ch["challengeId"], "sig": C.sign(sk, ["tt1-auth", ch["challenge"], RELAY])})
    if st != 200:
        raise Fail(r.get("error") or f"relay said {st}")
    save("token.json", {"token": r["token"], "expires_at": r["expires_at"], "user": r["user"]})
    return r["token"]


def api(method: str, path: str, body=None):
    st, r = http(method, path, body, token())
    if st == 401:
        st, r = http(method, path, body, token(force=True))
    if st >= 400:
        raise Fail(r.get("error") or f"relay said {st}")
    return r


def verifier() -> C.Verifier:
    v = C.Verifier(load("trust.json"), team_bytes(), {n: x["seq"] for n, x in load("seen.json", {}).items()})
    v.load_directory(api("GET", "/api/directory")["devices"])
    return v


def remember_seen(v: C.Verifier):
    old = load("seen.json", {})
    t = C.now_ms()
    for n, seq in v.seen.items():
        old.setdefault(n, {"seq": seq, "at": t})
    cutoff = t - 95 * 24 * 3600_000  # relay keeps 90 days
    save("seen.json", {n: x for n, x in old.items() if x["at"] > cutoff})


# ---------- commands ----------
WELL_KNOWN = "/.well-known/tabbie-team"


def check_relay(url: str) -> dict:
    """Is URL a team relay this Tabbie can talk to? Returns {relay, name} or raises Fail with a plain reason."""
    url = url.strip().rstrip("/")
    if "://" not in url:
        url = "https://" + url
    u = urllib.parse.urlsplit(url)
    local = u.hostname in ("localhost", "127.0.0.1")
    if u.scheme != "https" and not (local and u.scheme == "http"):
        raise Fail("The team relay must use https://")
    if not u.hostname or u.path not in ("", "/") or u.query or u.fragment or u.username:
        raise Fail("Use just the relay's address, like https://team.example.com")
    url = f"{u.scheme}://{u.netloc}"
    req = urllib.request.Request(url + WELL_KNOWN, headers={"user-agent": "tabbie-team/1", "accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            info = json.loads(r.read(4096) or b"{}")
    except urllib.error.HTTPError as e:
        raise Fail(f"That address answered {e.code}: it isn't a Tabbie team relay.") from None
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise Fail(f"Couldn't reach {u.hostname} ({type(getattr(e, 'reason', e)).__name__}).") from None
    except ValueError:
        raise Fail("That address isn't a Tabbie team relay.") from None
    if not isinstance(info, dict) or info.get("protocol") != C.PROTOCOL:
        raise Fail("That address isn't a Tabbie team relay (or speaks a newer version this Tabbie doesn't know).")
    name = "".join(ch for ch in str(info.get("name") or u.hostname) if ch.isprintable())[:40]
    out = {"relay": url, "name": name}
    mail = str(info.get("mail") or "")
    if mail.startswith("https://") and len(mail) < 300 and not any(c.isspace() for c in mail):
        out["mail"] = {"url": mail}  # the team's mail endpoint (see mailroom.py); each Mac still needs registering
    return out


def cmd_connect(args):
    """tt connect URL: check URL is a team relay and use it. Changing relay unlinks this Mac from the old one."""
    if not args:
        raise Fail("usage: tt connect URL")
    info = check_relay(args[0])
    cfg = config()
    if cfg.get("relay") and cfg["relay"] != info["relay"] and load("mac.json"):
        raise Fail(f"This Mac is linked to {cfg.get('name') or cfg['relay']}. Disconnect first (tt disconnect).")
    cfg.update(info)
    save("config.json", cfg)
    global RELAY
    RELAY = info["relay"]
    out(info, f"Using {info['name']} ({info['relay']}).")


def cmd_disconnect(_args):
    """Forget the relay and this Mac's keys here (the relay keeps history; remove the Mac there too)."""
    for f in ("mac.json", "trust.json", "team.json", "token.json", "seen.json", "mirror.json", "pairing.json", "me.json", "joining.json", "invites.json", "needkey.json"):
        (HOME / f).unlink(missing_ok=True)
    cfg = config()
    for k in ("relay", "name"):
        cfg.pop(k, None)
    save("config.json", cfg)
    out({"ok": True}, "Disconnected.")


JSON_OUT = False


def out(data, text: str):
    print(json.dumps(data) if JSON_OUT else text, flush=True)


def ensure_mac_keys(name: str) -> dict:
    m = load("mac.json")
    if not m:
        sk, xk = C.new_key(), C.new_key()
        sp = C.pub_raw(sk.public_key())
        m = {"kid": C.kid_of(sp), "sign_pub": sp, "ecdh_pub": C.pub_raw(xk.public_key()), "sign": C.key_pem(sk), "ecdh": C.key_pem(xk), "name": name}
        save("mac.json", m)
    return m


def pair_start(name: str) -> dict:
    """Ask the relay for a pairing code. Non-blocking: call pair_check() until it says linked."""
    m = ensure_mac_keys(name)
    st, r = http("POST", "/api/macs/pair", {"name": name, "sign_pub": m["sign_pub"], "ecdh_pub": m["ecdh_pub"]})
    if st == 409:
        return pair_finish()
    if st != 200:
        raise Fail(r.get("error") or f"relay said {st}")
    p = {"code": r["code"], "fingerprint": r["fingerprint"], "expires_at": int(time.time()) + 600, "relay": RELAY}
    save("pairing.json", p)
    return {"state": "waiting", **p}


def pair_check() -> dict:
    m = load("mac.json")
    if m and team()["current"] and load("trust.json"):
        return {"state": "linked", "owner": load("trust.json")["owner"], "fingerprint": C.fingerprint(m["sign_pub"])}
    nk = load("needkey.json")
    if m and nk and load("trust.json"):  # signed in, waiting for an invite code from a teammate
        if receive_wraps():
            (HOME / "needkey.json").unlink(missing_ok=True)
            return {"state": "linked", "owner": load("trust.json")["owner"], "fingerprint": C.fingerprint(m["sign_pub"])}
        return {"state": "needs_invite", "owner": load("trust.json")["owner"], "teammates": nk.get("teammates", []),
                "fingerprint": C.fingerprint(m["sign_pub"])}
    j = load("joining.json")
    if m and j:
        if receive_wraps():
            (HOME / "joining.json").unlink(missing_ok=True)
            return {"state": "linked", "owner": load("trust.json")["owner"], "fingerprint": C.fingerprint(m["sign_pub"])}
        return {"state": "joining", "inviter": j["inviter"], "fingerprint": C.fingerprint(m["sign_pub"])}
    p = load("pairing.json")
    if not m or not p:
        return {"state": "none"}
    if time.time() > p["expires_at"]:
        return {"state": "expired"}
    st, _ = http("POST", "/api/mac/challenge", {"kid": m["kid"]})
    if st != 200:
        return {"state": "waiting", **p}
    return pair_finish()


def pair_finish() -> dict:
    m = load("mac.json")
    token(force=True)
    owner = load("token.json")["user"]
    trust = load("trust.json") or {"owner": owner, "mac": {"kid": m["kid"], "sign_pub": m["sign_pub"]}, "allow": {}, "macs": {}}
    trust["owner"], trust["mac"] = owner, {"kid": m["kid"], "sign_pub": m["sign_pub"]}
    save("trust.json", trust)
    made = False
    if not team()["current"] and not receive_wraps():
        mates = teammates(owner)
        if mates:  # the team already exists: its key has to come from a teammate's Mac, by an invite. Never start a second one.
            save("needkey.json", {"teammates": mates, "at": C.now_ms()})
            (HOME / "pairing.json").unlink(missing_ok=True)
            return {"state": "needs_invite", "owner": owner, "teammates": mates, "fingerprint": C.fingerprint(m["sign_pub"])}
        new_team_key()
        made = True
    (HOME / "pairing.json").unlink(missing_ok=True)
    run_hook("trusted", owner, m["kid"], m["sign_pub"])  # e.g. this Mac's mailbox, after linking again with new keys
    return {"state": "linked", "owner": owner, "fingerprint": C.fingerprint(m["sign_pub"]), "new_team_key": made}


def teammates(owner: str) -> list[str]:
    """Other people with a linked Mac on this relay (so the team, and its key, already exist)."""
    return sorted({d["owner"] for d in api("GET", "/api/directory")["devices"]
                   if d.get("kind") == "mac" and d.get("status") == "approved" and d.get("owner") and d["owner"] != owner})


def cmd_pair(args):
    name = args[args.index("--name") + 1] if "--name" in args else f"{platform.node().split('.')[0] or 'Mac'}"
    if "--check" in args:
        r = pair_check()
        out(r, r["state"])
        return
    r = pair_start(name)
    if r["state"] == "needs_invite":
        out(r, f"Signed in as {r['owner']}. The team's key comes from a teammate's Mac: ask {' or '.join(r.get('teammates') or ['a teammate'])} for an invite code (Settings → Team → Invite on their Mac), then run: tt join CODE")
        return
    if "--start" in args or r["state"] == "linked":
        out(r, f"Code {r.get('code')}" if r["state"] == "waiting" else f"Linked as {r['owner']}.")
        return
    print(f"\n  Open {RELAY}, sign in, and enter this code under 'Link your Mac':\n\n      {r['code']}\n")
    print(f"  The page will show this Mac's fingerprint: {r['fingerprint']}. Check it matches.\n")
    print("  Waiting (10 minutes)…", flush=True)
    while True:
        r = pair_check()
        if r["state"] == "linked":
            break
        if r["state"] == "needs_invite":
            print("  " + f"Signed in as {r['owner']}. The team's key comes from a teammate's Mac: ask {' or '.join(r.get('teammates') or ['a teammate'])} for an invite code (Settings → Team → Invite on their Mac), then run: tt join CODE")
            return
        if r["state"] != "waiting":
            raise Fail("Timed out. Run tt pair again.")
        time.sleep(3)
    if r.get("new_team_key"):
        print("  Made a new team key on this Mac.")
    print(f"  Linked as {r['owner']}. Fingerprint {r['fingerprint']}.")


def receive_wraps() -> int:
    """Team keys another trusted Mac wrapped for this one (e.g. Aaron's Mac sharing with Leo's)."""
    return len(receive_new_keys())


def receive_new_keys() -> list[str]:
    """Like receive_wraps, returning the new key ids. The newest wrap becomes the current key."""
    m, _, xk = mac_keys()
    trust = load("trust.json")
    t = team()
    new = []
    for w in sorted(api("GET", "/api/wraps")["wraps"], key=lambda w: w.get("created_at") or 0):
        wrapper = next((x for x in trust.get("macs", {}).values() if x["kid"] == w["wrapper_kid"]), None)
        if not wrapper or w["key_id"] in t["keys"]:
            continue
        t["keys"][w["key_id"]] = C.b64u(C.unwrap(w, m["kid"], xk, wrapper["sign_pub"]))
        t["current"] = w["key_id"]
        new.append(w["key_id"])
    if new:
        save("team.json", t)
    return new


def sync_keys() -> list[str]:
    """A teammate's Mac changed the team key (a device was removed, or someone left): pick it up and pass it
    on to this person's own approved browsers and phones, which only ever get keys from their own Mac."""
    new = receive_new_keys()
    if new:
        trust = load("trust.json") or {}
        rec = [(k, d["ecdh_pub"]) for k, d in trust.get("allow", {}).items()]
        if rec:
            api("POST", "/api/wraps", {"wraps": wraps_for(rec, new)})
    return new


def new_team_key() -> str:
    t = team()
    key_id = "k" + C.b64u(os.urandom(9))
    t["keys"][key_id] = C.b64u(os.urandom(32))
    t["current"] = key_id
    save("team.json", t)
    return key_id


def wraps_for(recipients: list[tuple[str, str]], key_ids: list[str]) -> list[dict]:
    m, sk, _ = mac_keys()
    keys = team()["keys"]
    return [C.wrap(C.unb64u(keys[k]), k, kid, ecdh, m["kid"], sk) for kid, ecdh in recipients for k in key_ids]


def find_pending(code: str) -> dict:
    code = code.upper().replace(" ", "")
    pending = api("GET", "/api/devices")["pending"]
    hit = [p for p in pending if p["fingerprint"].replace("-", "") == code.replace("-", "")]
    if not hit:
        names = ", ".join(f"{p['name']} ({p['fingerprint']})" for p in pending) or "none"
        raise Fail(f"No waiting device shows {code}. Waiting: {names}")
    p = hit[0]
    if C.kid_of(p["sign_pub"]) != p["kid"] or C.fingerprint(p["sign_pub"]) != p["fingerprint"]:
        raise Fail("The relay's answer doesn't add up. Not approving.")
    return p


def cmd_approve(args):
    if not args:
        pending = api("GET", "/api/devices")["pending"]
        if not pending:
            print("No devices waiting.")
        for p in pending:
            print(f"  {p['name']}: {p['fingerprint']}")
        print("Approve with: tt approve <the code the browser shows>")
        return
    msg = approve_code(args[0])
    out({"ok": True, "message": msg}, msg)


def approve_code(code: str) -> str:
    p = find_pending(code)
    trust = load("trust.json")
    _, sk, _ = mac_keys()
    issued = C.now_ms()
    cert = C.sign(sk, C.cert_parts(p["kid"], trust["owner"], "browser", p["name"], p["sign_pub"], p["ecdh_pub"], issued))
    t = team()
    api("POST", f"/api/devices/{p['kid']}/approve",
        {"issued_at": issued, "cert_sig": cert, "wraps": wraps_for([(p["kid"], p["ecdh_pub"])], list(t["keys"]))})
    trust["allow"][p["kid"]] = {"sign_pub": p["sign_pub"], "ecdh_pub": p["ecdh_pub"], "name": p["name"]}
    save("trust.json", trust)
    try:
        share_me_key()
    except (Fail, OSError, ValueError, KeyError):
        pass  # the daemon shares it on its next start
    return f"Approved {p['name']} ({p['fingerprint']})."


def cmd_pending(args):
    """For Tabbie's approval sheet: who's waiting. Deliberately no fingerprints: you type the one the device shows."""
    m = load("mac.json")
    out = {"linked": bool(m and team()["current"]), "owner": (load("trust.json") or {}).get("owner"), "pending": []}
    if out["linked"]:
        out["pending"] = [{"name": p["name"], "since": p["created_at"]} for p in api("GET", "/api/devices")["pending"]]
    print(json.dumps(out))


def cmd_revoke(args):
    if not args:
        raise Fail("usage: tt revoke CODE")
    trust = load("trust.json")
    code = args[0].upper().replace("-", "")
    hit = [(k, d) for k, d in trust["allow"].items() if C.fingerprint(d["sign_pub"]).replace("-", "") == code]
    if not hit:
        raise Fail("None of your approved devices has that code. See: tt status")
    kid, d = hit[0]
    _, sk, _ = mac_keys()
    at = C.now_ms()
    api("POST", f"/api/devices/{kid}/revoke", {"at": at, "sig": C.sign(sk, C.revoke_parts(kid, trust["owner"], at))})
    trust.setdefault("removed", {})[kid] = dict(trust["allow"].pop(kid), removed_at=at)
    save("trust.json", trust)
    rotate()
    (HOME / "me.json").unlink(missing_ok=True)  # the removed device had the personal key too: make a new one
    share_me_key()
    out({"ok": True, "message": f"Removed {d['name']}."}, f"Removed {d['name']}.")


def cmd_rotate(_args):
    print(rotate())


def rotate() -> str:
    key_id = new_team_key()
    trust = load("trust.json")
    rec = [(k, d["ecdh_pub"]) for k, d in trust["allow"].items()]
    directory = {r["kid"]: r for r in api("GET", "/api/directory")["devices"]}
    for owner, mac in trust.get("macs", {}).items():
        r = directory.get(mac["kid"])
        if r and r["status"] == "approved" and r["sign_pub"] == mac["sign_pub"]:
            rec.append((mac["kid"], r["ecdh_pub"]))
    if rec:
        api("POST", "/api/wraps", {"wraps": wraps_for(rec, [key_id])})
    return f"New team key {key_id}, shared with {len(rec)} device(s)."


def cmd_trust(args):
    if len(args) != 2:
        raise Fail("usage: tt trust OWNER CODE")
    owner, code = args[0].lower(), args[1].upper().replace("-", "")
    trust = load("trust.json")
    if owner == trust["owner"]:
        raise Fail("That's you.")
    rows = [r for r in api("GET", "/api/directory")["devices"]
            if r["owner"] == owner and r["kind"] == "mac" and r["status"] == "approved"
            and C.fingerprint(r["sign_pub"]).replace("-", "") == code and C.kid_of(r["sign_pub"]) == r["kid"]]
    if not rows:
        raise Fail(f"No linked Mac for {owner} has that code.")
    r = rows[0]
    trust.setdefault("macs", {})[owner] = {"kid": r["kid"], "sign_pub": r["sign_pub"]}
    save("trust.json", trust)
    api("POST", "/api/wraps", {"wraps": wraps_for([(r["kid"], r["ecdh_pub"])], list(team()["keys"]))})
    print(f"Trusted {owner}'s Mac and shared the team key.")


# ---------- invites (see ttcore: make_invite / invite_proof) ----------
INVITE_HOURS = 24


def invite(for_name: str) -> dict:
    """Make a one-time invite code for a teammate's Mac. Their Tabbie needs only this code."""
    m, _, _ = mac_keys()
    trust = load("trust.json") or {}
    if not team()["current"] or not trust.get("owner"):
        raise Fail("Link this Mac to the team first.")
    name = for_name.strip().lower()
    enrol = add_account(name)
    secret = os.urandom(32)
    tok = C.invite_token(secret)
    r = api("POST", "/api/macs/invites", {"for": name, "token_hash": C.token_hash(tok)})
    inv = load("invites.json", {})
    inv[C.token_hash(tok)] = {"for": r["for"], "secret": C.b64u(secret), "at": C.now_ms()}
    save("invites.json", inv)
    code = C.make_invite(RELAY, r["for"], secret, {"owner": trust["owner"], "kid": m["kid"]})
    return {"code": code, "for": r["for"], "expires_at": r["expires_at"], "enrol": enrol}


def add_account(name: str) -> str:
    """Make sure `name` has a team account. Returns a one-time passkey link for their phone or browser when the
    account has no passkey yet, else "". (An existing account never gets a link: no one can take one over.)"""
    tok = C.b64u(os.urandom(32))
    try:
        r = api("POST", "/api/users", {"name": name, "token_hash": C.token_hash(tok)})
    except Fail as e:
        if "Not found" in str(e):  # an older relay: accounts are made with its scripts/invite.sh
            return ""
        raise
    return f"{RELAY}/enrol#{tok}" if r.get("enrol") else ""


def remove_teammate(name: str) -> str:
    """Remove someone from the team: the relay revokes their devices and passkeys, then a new team key goes
    only to the people who remain (they can't read anything shared from now on)."""
    name = name.strip().lower()
    trust = load("trust.json") or {}
    if name == trust.get("owner"):
        raise Fail("You can't remove yourself. Use Disconnect instead.")
    api("POST", f"/api/users/{name}/remove", {})
    trust.get("macs", {}).pop(name, None)
    trust.setdefault("removed_people", []).append(name)
    save("trust.json", trust)
    rotate()
    return f"Removed {name}. The team has a new key that {name} doesn't have."


def cmd_remove(args):
    if len(args) != 1:
        raise Fail("usage: tt remove NAME")
    msg = remove_teammate(args[0])
    out({"ok": True, "message": msg}, msg)


def cmd_invite(args):
    if len(args) != 1:
        raise Fail("usage: tt invite NAME")
    r = invite(args[0])
    extra = (f"\nThey also need this link to sign in on their phone or browser (works once, {INVITE_HOURS} hours):\n\n{r['enrol']}\n"
             if r.get("enrol") else "")
    out(r, f"Send {r['for']} this code (works once, for {INVITE_HOURS} hours). Their Tabbie asks for it in setup or Settings → Team:\n\n{r['code']}\n{extra}")


def join(code: str, name: str | None = None) -> dict:
    """Use an invite: connect to its relay, link this Mac to the invited account, pin the inviting Mac.
    The team key arrives once the inviting Mac has checked the proof (settle_invites on their side)."""
    global RELAY
    try:
        inv = C.read_invite(code)
    except ValueError as e:
        raise Fail(str(e)) from None
    info_ = check_relay(inv["relay"])
    cfg = config()
    if load("mac.json") and load("trust.json") and team()["current"]:
        raise Fail(f"This Mac is already linked to {cfg.get('name') or cfg.get('relay')}. Disconnect first in Settings → Team.")
    cfg.update(info_)
    save("config.json", cfg)
    RELAY = info_["relay"]
    if not team()["current"]:  # an earlier sign-in or join without the key: start this Mac afresh (the relay won't relink a linked Mac)
        for f in ("mac.json", "needkey.json", "joining.json"):
            (HOME / f).unlink(missing_ok=True)
    for f in ("pairing.json", "token.json", "trust.json"):
        (HOME / f).unlink(missing_ok=True)
    m = ensure_mac_keys(name or platform.node().split(".")[0] or "Mac")
    st, r = http("POST", "/api/macs/pair", {"name": m["name"], "sign_pub": m["sign_pub"], "ecdh_pub": m["ecdh_pub"],
                                            "invite": inv["token"], "proof": C.invite_proof(inv["secret"], m["sign_pub"], m["ecdh_pub"])})
    if st != 200 or not r.get("linked"):
        raise Fail(r.get("error") or f"The team relay said {st}.")
    token(force=True)
    owner = load("token.json")["user"]
    if owner != inv["for"]:
        raise Fail("The relay linked this Mac to a different account than the invite was for. Not continuing.")
    who = inv["inviter"]
    row = next((d for d in api("GET", "/api/directory")["devices"] if d["kid"] == who["kid"]), None)
    if not row or row["status"] != "approved" or C.kid_of(row["sign_pub"]) != who["kid"] or row["owner"] != who["owner"] or row["kind"] != "mac":
        raise Fail("The Mac that made this invite isn't linked any more. Ask for a new invite.")
    save("trust.json", {"owner": owner, "mac": {"kid": m["kid"], "sign_pub": m["sign_pub"]}, "allow": {},
                        "macs": {who["owner"]: {"kid": who["kid"], "sign_pub": row["sign_pub"]}}})
    save("joining.json", {"inviter": who["owner"], "at": C.now_ms()})
    return {**pair_check(), "team": info_["name"], "owner": owner}


def cmd_join(args):
    if not args:
        raise Fail("usage: tt join CODE")
    r = join(args[0])
    msg = f"Linked to {r['team']} as {r['owner']}." + (" Waiting for " + r["inviter"] + "'s Mac to share the team key (needs it to be on)." if r["state"] == "joining" else "")
    out(r, msg)


def run_hook(name: str, *args: str):
    """config.json "hooks": {"trusted": ["cmd", "arg"...]}: run after this Mac trusts a Mac (OWNER KID SIGN_PUB: a
    teammate's, or this Mac itself after linking again), e.g. to give that Mac its mailbox. Public data only.
    Queued in hooks.json and retried (1 min, 2, 4 ... up to 6 h) until it succeeds, so a hook that fails once
    (offline, a service down) isn't lost. The daemon runs the queue every minute."""
    cmd = (config().get("hooks") or {}).get(name)
    if not isinstance(cmd, list) or not cmd:
        return
    q = load("hooks.json", {"pending": []})
    q["pending"] = [p for p in q["pending"] if not (p["name"] == name and p["args"][:1] == list(args[:1]))]  # newest per owner
    q["pending"].append({"name": name, "args": list(args), "tries": 0, "next": 0})
    save("hooks.json", q)
    import threading
    threading.Thread(target=run_pending_hooks, daemon=True, name="hooks").start()


_HOOK_LOCK = __import__("threading").Lock()


def run_pending_hooks(now: float | None = None) -> list[str]:
    """Run queued hooks that are due. Returns the names that succeeded."""
    import subprocess
    if not _HOOK_LOCK.acquire(blocking=False):
        return []
    try:
        q = load("hooks.json", {"pending": []})
        hooks, done, keep = config().get("hooks") or {}, [], []
        for p in q["pending"]:
            cmd = hooks.get(p["name"])
            if not isinstance(cmd, list) or not cmd:
                continue  # hook removed from config: drop it
            if (now or time.time()) < p.get("next", 0):
                keep.append(p)
                continue
            try:
                r = subprocess.run([*map(str, cmd), *p["args"]], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=180)
                ok, why = r.returncode == 0, (r.stdout + r.stderr).strip()[-200:]
            except (OSError, subprocess.TimeoutExpired) as e:
                ok, why = False, type(e).__name__
            if ok:
                done.append(p["name"])
                print(f"hook {p['name']} {p['args'][0] if p['args'] else ''}: done", flush=True)
                continue
            p["tries"] = p.get("tries", 0) + 1
            p["next"] = (now or time.time()) + min(60 * 2 ** (p["tries"] - 1), 6 * 3600)
            p["error"] = why
            print(f"hook {p['name']} {p['args'][0] if p['args'] else ''}: failed ({why[:120]}); trying again later", flush=True)
            keep.append(p)
        q2 = load("hooks.json", {"pending": []})  # keep anything queued while these ran
        had = {(x["name"], tuple(x["args"])) for x in q["pending"]}
        q["pending"] = keep + [x for x in q2["pending"] if (x["name"], tuple(x["args"])) not in had]
        save("hooks.json", q)
        return done
    finally:
        _HOOK_LOCK.release()


def settle_invites() -> list[str]:
    """Inviting side: trust each Mac that joined with one of our invites and proved it holds the secret, then
    share the team key with it. Unused invites drop after a day. Returns the owners newly trusted."""
    inv = load("invites.json", {})
    if not inv:
        return []
    done, keep = [], {}
    rows = {r["token_hash"]: r for r in api("GET", "/api/macs/invites")["invites"]}
    trust = load("trust.json")
    for h, x in inv.items():
        r = rows.get(h)
        if not r or not r.get("used_at"):
            if r and C.now_ms() - x["at"] < INVITE_HOURS * 3600_000:
                keep[h] = x
            continue
        ok = (r.get("status") == "approved" and r.get("sign_pub") and C.kid_of(r["sign_pub"]) == r["mac_kid"]
              and r.get("owner") == x["for"] and C.proof_ok(C.unb64u(x["secret"]), r["sign_pub"], r["ecdh_pub"], r.get("proof")))
        if not ok:
            print(f"invite for {x['for']}: the Mac that used it couldn't prove it had the code. Not trusted.", flush=True)
            continue
        trust.setdefault("macs", {})[r["owner"]] = {"kid": r["mac_kid"], "sign_pub": r["sign_pub"]}
        save("trust.json", trust)
        api("POST", "/api/wraps", {"wraps": wraps_for([(r["mac_kid"], r["ecdh_pub"])], list(team()["keys"]))})
        done.append(r["owner"])
        run_hook("trusted", r["owner"], r["mac_kid"], r["sign_pub"])
    save("invites.json", keep)
    return done


def cmd_mail(args):
    import mailroom as M

    if not args:
        raise Fail("usage: tt mail list|read|send|draft|triage (see tt --help)")
    op, rest = args[0], args[1:]

    def opt(name, default=None):
        return rest[rest.index(name) + 1] if name in rest and rest.index(name) + 1 < len(rest) else default
    try:
        if op == "list":
            folder = rest[0] if rest and not rest[0].startswith("--") else "inbox"
            r = M.handle(sys.modules[__name__], {"op": "list", "folder": folder, "q": opt("--q", "")})
            if JSON_OUT:
                return out(r, "")
            print(f"{r['mailbox']}: {r['unread']} unread, {r['drafts']} drafts waiting, {r['followups']} follow-ups suggested")
            for x in r["messages"]:
                flag = " " if x.get("read_at") or x["direction"] == "out" else "*"
                note = f"  [{x['note']['verdict']}: {x['note']['text'][:80]}]" if x.get("note") else ""
                print(f"{flag} {x['id']}  {x['created_at'][:16]}  {(x.get('from_name') or x['from_address'])[:28]:28}  {(x.get('subject') or '')[:60]}{note}")
        elif op == "read":
            r = M.handle(sys.modules[__name__], {"op": "thread", "id": rest[0]})
            if JSON_OUT:
                return out(r, "")
            print(M.thread_text(r, 200_000))
            if r.get("draft"):
                print(f"\nDRAFT waiting ({r['draft']['by']}):\nTo: {', '.join(r['draft']['to'])}\nSubject: {r['draft']['subject']}\n\n{r['draft']['text']}")
        elif op == "send":
            text = Path(opt("--file")).read_text() if opt("--file") else opt("--text", "")
            r = M.handle(sys.modules[__name__], {"op": "send", "to": (opt("--to") or "").split(","), "cc": [x for x in (opt("--cc") or "").split(",") if x],
                                                 "subject": opt("--subject", ""), "text": text, "reply_to_id": opt("--reply")})
            out(r, "Sent.")
        elif op == "draft":
            tid = None if not rest or rest[0] == "-" else rest[0]
            d = M.draft(sys.modules[__name__], " ".join(rest[1:]), tid)
            out(d, f"To: {', '.join(d['to'])}\nSubject: {d['subject']}\n\n{d['text']}")
        elif op == "triage":
            r = M.triage(sys.modules[__name__], force=True)
            out(r, "")
        else:
            raise Fail("usage: tt mail list|read|send|draft|triage")
    except M.MailError as e:
        raise Fail(str(e)) from None


def info() -> dict:
    """Everything Settings → Team shows. Never includes keys or tokens."""
    cfg, m, trust = config(), load("mac.json"), load("trust.json") or {}
    linked = bool(m and team()["current"] and trust)
    d = {"relay": cfg.get("relay"), "name": cfg.get("name"), "projects": cfg["projects"], "linked": linked,
         "pairing": None, "owner": trust.get("owner"), "mac": None, "devices": [], "macs": []}
    if m:
        d["mac"] = {"name": m["name"], "fingerprint": C.fingerprint(m["sign_pub"])}
    if not linked and load("pairing.json"):
        d["pairing"] = pair_check()
        d["linked"] = d["pairing"]["state"] == "linked"
    d["devices"] = [{"name": x["name"], "fingerprint": C.fingerprint(x["sign_pub"])} for x in trust.get("allow", {}).values()]
    d["macs"] = [{"owner": o, "fingerprint": C.fingerprint(x["sign_pub"])} for o, x in trust.get("macs", {}).items()]
    d["invites"] = [{"for": x["for"], "at": x["at"]} for x in (load("invites.json", {}) or {}).values()]
    if (load("joining.json") or load("needkey.json")) and m and not team()["current"]:
        d["pairing"] = pair_check()
        d["linked"] = d["pairing"]["state"] == "linked"
    return d


def cmd_info(_args):
    print(json.dumps(info()))


def cmd_status(_args):
    m = load("mac.json")
    if not m:
        print("Not linked. Run: tt pair")
        return
    trust, t = load("trust.json") or {}, team()
    print(f"This Mac: {m['name']}  {C.fingerprint(m['sign_pub'])}  owner {trust.get('owner', '?')}")
    print(f"Relay: {RELAY}")
    print(f"Team keys: {len(t['keys'])} (current {t['current']})")
    print("Your approved devices:" + ("" if trust.get("allow") else " none"))
    for d in trust.get("allow", {}).values():
        print(f"  {d['name']}  {C.fingerprint(d['sign_pub'])}")
    for o, mac in trust.get("macs", {}).items():
        print(f"Trusted Mac: {o}  {C.fingerprint(mac['sign_pub'])}")


async def _ws(room: str, on_open=None, follow=True):
    import websockets

    url = RELAY.replace("http", "ws", 1) + f"/api/rooms/{room}/ws"
    v = verifier()
    last = ""
    async with websockets.connect(url, additional_headers={"authorization": f"Bearer {token()}"}, user_agent_header="tabbie-team/1") as ws:
        async def show(raw):
            nonlocal last
            msg = json.loads(raw)
            if "here" in msg or "caught_up" in msg:
                return
            if "rejected" in msg:
                print(f"  relay rejected our event: {msg['rejected']}")
                return
            last = C.body_hash(msg["body"])
            try:
                out = v.open(json.loads(msg["body"]), room, msg["seq"])
                text = out["payload"].get("text", json.dumps(out["payload"]))
                print(f"  #{msg['seq']} {msg['author']} ({out['device']}) ✓ {text}")
            except C.Rejected as e:
                print(f"  #{msg['seq']} REJECTED ({e}) from {msg['author']}")

        while True:  # backlog
            try:
                await show(await asyncio.wait_for(ws.recv(), 0.8))
            except asyncio.TimeoutError:
                break
        if on_open:
            m, sk, _ = mac_keys()
            t = team()
            e = C.seal(room, "status", load("trust.json")["owner"], m["kid"], sk, t["current"], C.unb64u(t["keys"][t["current"]]), on_open, prev=last)
            await ws.send(json.dumps(e))
            try:
                await show(await asyncio.wait_for(ws.recv(), 5))
            except asyncio.TimeoutError:
                print("  no echo from the relay")
        while follow:
            await show(await ws.recv())
    remember_seen(v)


def cmd_listen(args):
    try:
        asyncio.run(_ws(args[0] if args else "lobby"))
    except KeyboardInterrupt:
        pass


def cmd_send(args):
    if len(args) < 2:
        raise Fail("usage: tt send ROOM TEXT")
    asyncio.run(_ws(args[0], on_open={"text": " ".join(args[1:])}, follow=False))


_WATCH = None


def start_invite_watch():
    """While invites this Mac made are open, check every 10 s whether one was used (and settle it)."""
    global _WATCH
    if _WATCH and _WATCH.is_alive():
        return
    import threading

    def loop():
        while True:
            time.sleep(10)
            if not load("invites.json", {}):
                continue
            try:
                for who in settle_invites():
                    print(f"daemon: {who}'s Mac joined with an invite; trusted it and shared the team key", flush=True)
            except (Fail, OSError, ValueError, KeyError) as e:
                print(f"daemon: invite check failed ({type(e).__name__})", flush=True)

    _WATCH = threading.Thread(target=loop, daemon=True, name="invites")
    _WATCH.start()
    threading.Thread(target=_key_loop, daemon=True, name="keys").start()


KEY_EVERY = 60


def _key_loop():
    while True:
        try:
            run_pending_hooks()
        except (OSError, ValueError, KeyError) as e:
            print(f"daemon: hook queue failed ({type(e).__name__})", flush=True)
        try:
            for k in sync_keys():
                print(f"daemon: received new team key {k} and shared it with this person's devices", flush=True)
        except (Fail, OSError, ValueError, KeyError) as e:
            print(f"daemon: key check failed ({type(e).__name__})", flush=True)
        time.sleep(KEY_EVERY)


def cmd_daemon(_args):
    """Keep shared sessions mirrored to the relay (run by the LaunchAgent)."""
    import mirror

    global RELAY
    idle = False
    while True:
        cfg = config()
        RELAY = os.environ.get("TT_RELAY", cfg.get("relay") or "").rstrip("/")
        trust = load("trust.json") or {}
        if RELAY and load("mac.json") and load("joining.json"):
            try:
                pair_check()  # joined with an invite: pick up the team key once the inviting Mac shares it
            except (Fail, OSError, ValueError, KeyError):
                pass
        if not (RELAY and load("mac.json") and team()["current"] and trust.get("owner")):
            if not idle:
                print("daemon: idle until this Mac is linked to a team (Settings → Team)", flush=True)
                idle = True
            time.sleep(10)
            continue
        idle = False
        start_invite_watch()
        try:
            asyncio.run(mirror.Mirror(sys.modules[__name__], trust["owner"], cfg).run())
        except KeyboardInterrupt:
            return
        except Exception as e:  # noqa: BLE001 (stay up: log the kind of failure, retry)
            print(f"daemon: {type(e).__name__}: {str(e)[:200]}; restarting in 5 s", flush=True)
            time.sleep(5)


def cmd_share(args):
    """tt share PROJECT | tt unshare PROJECT | tt share  (list)"""
    cfg = config()
    if args:
        name = args[0]
        if name.lower() not in [p.lower() for p in cfg["projects"]]:
            cfg["projects"].append(name)
        save("config.json", cfg)
    out({"projects": cfg["projects"]}, "Shared projects: " + (", ".join(cfg["projects"]) or "none"))


def cmd_unshare(args):
    cfg = config()
    if args:
        cfg["projects"] = [p for p in cfg["projects"] if p.lower() != args[0].lower()]
        save("config.json", cfg)
    out({"projects": cfg["projects"]}, "Shared projects: " + (", ".join(cfg["projects"]) or "none"))


COMMANDS = {"connect": cmd_connect, "disconnect": cmd_disconnect, "info": cmd_info, "pair": cmd_pair, "status": cmd_status, "approve": cmd_approve, "revoke": cmd_revoke, "rotate": cmd_rotate,
            "trust": cmd_trust, "listen": cmd_listen, "send": cmd_send, "daemon": cmd_daemon, "share": cmd_share, "unshare": cmd_unshare,
            "pending": cmd_pending, "invite": cmd_invite, "remove": cmd_remove, "join": cmd_join, "mail": cmd_mail}

if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)
    a = sys.argv[1:]
    if "--json" in a:
        JSON_OUT = True
        a.remove("--json")
    if not a or a[0] not in COMMANDS:
        print(__doc__)
        sys.exit(2)
    try:
        COMMANDS[a[0]](a[1:])
    except Fail as e:
        print(f"tt: {e}", file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, KeyError) as e:  # network, bad relay answer, missing state: say what kind, not a traceback
        print(f"tt: {type(e).__name__}: {str(e)[:200]}", file=sys.stderr)
        sys.exit(1)
