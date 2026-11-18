"""Shared-Tabbie protocol core for the Mac: keys, signatures, team-key wrapping, event encryption and the
verifier that decides what this Mac trusts. No network here (see tt.py). Spec: docs/team/PROTOCOL.md.

The Mac is the root of trust for its owner. It trusts:
  - itself,
  - its owner's browser devices that it approved itself (local allowlist, never the relay's list),
  - other people's Macs only after their fingerprint was pinned on this Mac (`tt trust`),
  - other people's devices only through a certificate signed by their pinned Mac, minus signed revocations.
"""
from __future__ import annotations

PROTOCOL = "tt1"  # what /.well-known/tabbie-team must say

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature, encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

CURVE = ec.SECP256R1()
EXEC_TYPES = {"prompt", "approval"}  # events that can make a Mac do something: tight clock window
EXEC_WINDOW_MS = 2 * 60 * 1000
CROCK = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


# ---------- encoding ----------
def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def canon(parts: list) -> bytes:
    return json.dumps(parts, separators=(",", ":"), ensure_ascii=False).encode()


def now_ms() -> int:
    return int(time.time() * 1000)


# ---------- keys ----------
def pub_raw(pub: ec.EllipticCurvePublicKey) -> str:
    return b64u(pub.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))


def load_pub(raw: str) -> ec.EllipticCurvePublicKey:
    return ec.EllipticCurvePublicKey.from_encoded_point(CURVE, unb64u(raw))


def new_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(CURVE)


def key_pem(k: ec.EllipticCurvePrivateKey) -> str:
    return k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()


def load_key(pem: str) -> ec.EllipticCurvePrivateKey:
    return serialization.load_pem_private_key(pem.encode(), None)


def kid_of(sign_pub: str) -> str:
    return b64u(hashlib.sha256(unb64u(sign_pub)).digest())[:22]


def fingerprint(sign_pub: str) -> str:
    d = hashlib.sha256(unb64u(sign_pub)).digest()
    bits = int.from_bytes(d[:8], "big")
    s = "".join(CROCK[(bits >> (64 - 5 * (i + 1))) & 31] for i in range(12))
    return f"{s[:4]}-{s[4:8]}-{s[8:12]}"


# ---------- signatures (ECDSA P-256 / SHA-256, raw r||s like WebCrypto) ----------
def sign(k: ec.EllipticCurvePrivateKey, parts: list) -> str:
    r, s = decode_dss_signature(k.sign(canon(parts), ec.ECDSA(hashes.SHA256())))
    return b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


def verify(sign_pub: str, sig: str, parts: list) -> bool:
    try:
        raw = unb64u(sig)
        if len(raw) != 64:
            return False
        der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
        load_pub(sign_pub).verify(der, canon(parts), ec.ECDSA(hashes.SHA256()))
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def cert_parts(kid, owner, kind, name, sign_pub, ecdh_pub, issued_at) -> list:
    return ["tt1-cert", kid, owner, kind, name, sign_pub, ecdh_pub, issued_at]


def revoke_parts(kid, owner, at) -> list:
    return ["tt1-revoke", kid, owner, at]


# ---------- invites: one code that links a teammate's Mac and pins the inviting Mac ----------
# TT1-<b64url(JSON)>. Holds the relay address, a one-time token (the relay stores only its hash), a secret
# the relay never sees, and the inviting Mac's public keys. The joining Mac pins the inviting Mac from it and
# proves it holds the secret (proof below); the inviting Mac checks that proof before sharing the team key.
INVITE_PREFIX = "TT1-"


def make_invite(relay: str, for_name: str, secret: bytes, inviter: dict) -> str:
    body = {"r": relay, "u": for_name, "s": b64u(secret), "o": inviter["owner"], "k": inviter["kid"]}
    return INVITE_PREFIX + b64u(json.dumps(body, separators=(",", ":")).encode())


def read_invite(code: str) -> dict:
    """Parse and sanity-check an invite code. Raises ValueError with a plain reason."""
    code = "".join(str(code or "").split())
    if not code.upper().startswith(INVITE_PREFIX):
        raise ValueError("That isn't a Tabbie team invite code (they start with TT1-).")
    try:
        d = json.loads(unb64u(code[len(INVITE_PREFIX):]))
    except (ValueError, TypeError):
        raise ValueError("That invite code is incomplete. Copy all of it and try again.") from None
    if not isinstance(d, dict) or not all(isinstance(d.get(x), str) and d[x] for x in ("r", "u", "s", "o", "k")):
        raise ValueError("That invite code is incomplete. Copy all of it and try again.")
    secret = unb64u(d["s"])
    if len(secret) != 32 or len(d["k"]) != 22:
        raise ValueError("That invite code doesn't add up. Ask for a new one.")
    return {"relay": d["r"], "for": d["u"], "secret": secret, "token": invite_token(secret),
            "inviter": {"owner": d["o"], "kid": d["k"]}}


def invite_token(secret: bytes) -> str:
    """The one-time token the relay sees, derived from the secret (the relay can't get the secret back from it)."""
    return b64u(hmac.new(secret, b"tt1-invite-token", hashlib.sha256).digest())


def token_hash(token: str) -> str:
    """What the relay stores for a one-time token (matches its sha256(): b64url of SHA-256 of the text)."""
    return b64u(hashlib.sha256(token.encode()).digest())


def invite_proof(secret: bytes, sign_pub: str, ecdh_pub: str) -> str:
    return b64u(hmac.new(secret, canon(["tt1-invite", sign_pub, ecdh_pub]), hashlib.sha256).digest())


def proof_ok(secret: bytes, sign_pub: str, ecdh_pub: str, proof: str) -> bool:
    return isinstance(proof, str) and hmac.compare_digest(invite_proof(secret, sign_pub, ecdh_pub), proof)


def wrap_parts(key_id, recipient_kid, epk, iv, ct, wrapper_kid) -> list:
    return ["tt1-wrap", key_id, recipient_kid, epk, iv, ct, wrapper_kid]


# ---------- team key wrapping (ECDH P-256 -> HKDF-SHA256 -> AES-256-GCM) ----------
def _wrap_key(shared: bytes, epk: str) -> bytes:
    return HKDF(hashes.SHA256(), 32, salt=unb64u(epk), info=b"tt1-wrap").derive(shared)


def wrap(team_key: bytes, key_id: str, recipient_kid: str, recipient_ecdh_pub: str,
         wrapper_kid: str, wrapper_sign: ec.EllipticCurvePrivateKey) -> dict:
    eph = new_key()
    epk = pub_raw(eph.public_key())
    k = _wrap_key(eph.exchange(ec.ECDH(), load_pub(recipient_ecdh_pub)), epk)
    iv = os.urandom(12)
    ct = AESGCM(k).encrypt(iv, team_key, f"{key_id}|{recipient_kid}".encode())
    w = {"key_id": key_id, "recipient_kid": recipient_kid, "epk": epk, "iv": b64u(iv), "ct": b64u(ct)}
    w["sig"] = sign(wrapper_sign, wrap_parts(key_id, recipient_kid, epk, w["iv"], w["ct"], wrapper_kid))
    return w


def unwrap(w: dict, my_kid: str, my_ecdh: ec.EllipticCurvePrivateKey, wrapper_sign_pub: str) -> bytes:
    if w["recipient_kid"] != my_kid:
        raise ValueError("wrap is for another device")
    if not verify(wrapper_sign_pub, w["sig"], wrap_parts(w["key_id"], my_kid, w["epk"], w["iv"], w["ct"], w["wrapper_kid"])):
        raise ValueError("wrap signature doesn't check out")
    k = _wrap_key(my_ecdh.exchange(ec.ECDH(), load_pub(w["epk"])), w["epk"])
    return AESGCM(k).decrypt(unb64u(w["iv"]), unb64u(w["ct"]), f"{w['key_id']}|{my_kid}".encode())


# ---------- events ----------
def event_aad(e: dict) -> bytes:
    return canon(["tt1-ev", e["room"], e["type"], e["author"], e["kid"], e["key_id"], e["nonce"], e["ts"], e["prev"]])


def event_parts(e: dict) -> list:
    return ["tt1-ev", e["room"], e["type"], e["author"], e["kid"], e["key_id"], e["nonce"], e["ts"], e["prev"], e["iv"], e["ct"]]


def body_hash(body: str) -> str:
    return b64u(hashlib.sha256(body.encode()).digest())[:22]


def seal(room: str, type_: str, author: str, kid: str, sign_key, key_id: str, team_key: bytes, payload: dict,
         prev: str = "", ts: int | None = None, nonce: str | None = None) -> dict:
    e = {"v": 1, "room": room, "type": type_, "author": author, "kid": kid, "key_id": key_id,
         "nonce": nonce or b64u(secrets.token_bytes(16)), "ts": ts if ts is not None else now_ms(), "prev": prev}
    iv = os.urandom(12)
    e["iv"] = b64u(iv)
    e["ct"] = b64u(AESGCM(team_key).encrypt(iv, json.dumps(payload).encode(), event_aad(e)))
    e["sig"] = sign(sign_key, event_parts(e))
    return e


class Rejected(Exception):
    pass


class Verifier:
    """Decides whether an event is genuine, and whether this Mac may act on it.

    trust = {
      "owner": "aaron", "mac": {"kid":…, "sign_pub":…},
      "allow": {kid: {"sign_pub":…, "name":…}},          # this owner's devices approved on this Mac
      "macs": {owner: {"kid":…, "sign_pub":…}},          # other people's Macs, pinned by fingerprint
    }
    directory = relay's /api/directory rows (used only for other people's devices, checked against their Mac)
    """

    def __init__(self, trust: dict, team_keys: dict[str, bytes], seen: dict[str, int] | None = None):
        # seen: nonce -> relay seq it arrived with. The same event again (same seq) is fine to show but never
        # runs twice; the same nonce under a new seq is a replay.
        self.trust, self.keys, self.seen = trust, team_keys, seen if seen is not None else {}
        self.others: dict[str, dict] = {}

    def load_directory(self, rows: list[dict]):
        """Accept other people's device certs only if their pinned Mac signed them; apply signed revocations."""
        others = {}
        for r in rows:
            mac = self.trust.get("macs", {}).get(r.get("owner"))
            if not mac or r.get("kind") != "browser" or not r.get("cert_sig"):
                continue
            if not verify(mac["sign_pub"], r["cert_sig"], cert_parts(r["kid"], r["owner"], r["kind"], r["name"], r["sign_pub"], r["ecdh_pub"], r["issued_at"])):
                continue
            if r.get("kid") != kid_of(r["sign_pub"]):
                continue
            revoked = r.get("revoke_sig") and verify(mac["sign_pub"], r["revoke_sig"], revoke_parts(r["kid"], r["owner"], r["revoked_at"]))
            if not revoked:
                others[r["kid"]] = {"owner": r["owner"], "sign_pub": r["sign_pub"], "name": r["name"]}
        self.others = others

    def signer(self, kid: str, author: str) -> dict | None:
        t = self.trust
        if author == t["owner"]:
            if kid == t["mac"]["kid"]:
                return {"sign_pub": t["mac"]["sign_pub"], "name": "this Mac"}
            if kid in t.get("allow", {}):
                return t["allow"][kid]
            gone = t.get("removed", {}).get(kid)  # history from a removed device: shown, flagged, never run
            return dict(gone, name=f"{gone['name']} (removed)", removed=True) if gone else None
        mac = t.get("macs", {}).get(author)
        if mac and kid == mac["kid"]:
            return {"sign_pub": mac["sign_pub"], "name": f"{author}'s Mac"}
        o = self.others.get(kid)
        return o if o and o["owner"] == author else None

    def open(self, e: dict, room: str, seq: int, now: int | None = None) -> dict:
        """Verify and decrypt. Returns {"payload", "device", "can_execute"}; raises Rejected otherwise."""
        now = now if now is not None else now_ms()
        need = {"v", "room", "type", "author", "kid", "key_id", "nonce", "ts", "prev", "iv", "ct", "sig"}
        if not isinstance(e, dict) or set(e) != need or e["v"] != 1:
            raise Rejected("malformed")
        if e["room"] != room:
            raise Rejected("wrong room")
        dev = self.signer(e["kid"], e["author"])
        if not dev:
            raise Rejected("unknown or untrusted device")
        if not verify(dev["sign_pub"], e["sig"], event_parts(e)):
            raise Rejected("bad signature")
        if dev.get("removed") and int(e["ts"]) >= dev["removed_at"]:
            raise Rejected("sent after the device was removed")
        again = e["nonce"] in self.seen
        if again and self.seen[e["nonce"]] != seq:
            raise Rejected("replay")
        key = self.keys.get(e["key_id"])
        if key is None:
            raise Rejected("unknown team key")
        try:
            payload = json.loads(AESGCM(key).decrypt(unb64u(e["iv"]), unb64u(e["ct"]), event_aad(e)))
        except (InvalidTag, ValueError):
            raise Rejected("can't decrypt") from None
        self.seen[e["nonce"]] = seq
        fresh = abs(now - int(e["ts"])) <= EXEC_WINDOW_MS
        mine = (e["type"] in EXEC_TYPES and e["author"] == self.trust["owner"] and e["kid"] != self.trust["mac"]["kid"]
                and not dev.get("removed"))
        if mine and not fresh and not again:
            raise Rejected("stale")
        return {"payload": payload, "device": dev.get("name", ""), "can_execute": mine and fresh and not again}
