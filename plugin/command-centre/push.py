"""Web Push: tell a phone (home-screen Tabbie) or browser when a job finishes or needs you, even with
Tabbie closed. The push itself is EMPTY: the push service (Apple / Google / Mozilla) only carries a
ping, and the extension's service worker (webui-extension/push-sw.js) then asks Tabbie, behind its
login, what happened (GET /push/latest). No chat text ever goes through a third party.

Keys: a VAPID key pair made once in state/push/ (0600, never in git). Subscriptions: state/push/subs.json.
Sending needs pywebpush (bin/cc-sidecar runs the sidecar with it); without it pushing is just off.
"""
from __future__ import annotations

import base64
import os
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from . import common

DIR = common.STATE_DIR / "push"
KEY = DIR / "vapid.pem"
SUBS = DIR / "subs.json"
LATEST = DIR / "latest.json"
MAX_SUBS = 20
KEEP = 30
# Only real push services: a subscription is a URL we POST to, so never let it point anywhere else.
PUSH_HOSTS = re.compile(r"(^|\.)(push\.apple\.com|fcm\.googleapis\.com|googleapis\.com|push\.services\.mozilla\.com|"
                        r"notify\.windows\.com)$")
CONTACT = "https://github.com"  # VAPID "sub": an https origin (no path) or a mailto: address
EVENTS = {"done": "done", "failed": "failed", "awaiting_answer": "question", "awaiting_answers": "question",
          "awaiting_approval": "plan", "limit_blocked": "limit", "claude_limited": "limit"}
_lock = threading.Lock()


def available() -> bool:
    try:
        import pywebpush  # noqa: F401
        import py_vapid  # noqa: F401
        return True
    except Exception:
        return False


def _vapid():
    from py_vapid import Vapid
    DIR.mkdir(parents=True, exist_ok=True)
    with _lock:
        if not KEY.exists():
            v = Vapid()
            v.generate_keys()
            v.save_key(str(KEY))
            os.chmod(KEY, 0o600)
    return Vapid.from_file(str(KEY))


def public_key() -> str | None:
    """The applicationServerKey for pushManager.subscribe (base64url, uncompressed P-256 point)."""
    if not available():
        return None
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    raw = _vapid().public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _subs() -> list[dict]:
    return [s for s in (common.read_json(SUBS, []) or []) if isinstance(s, dict) and s.get("endpoint")]


def _valid(sub) -> bool:
    if not isinstance(sub, dict):
        return False
    ep, keys = str(sub.get("endpoint") or ""), sub.get("keys") or {}
    u = urlsplit(ep)
    return (u.scheme == "https" and bool(u.hostname) and bool(PUSH_HOSTS.search(u.hostname)) and len(ep) < 1000
            and isinstance(keys, dict) and all(isinstance(keys.get(k), str) and 0 < len(keys[k]) < 200
                                               for k in ("p256dh", "auth")))


def subscribe(body: dict) -> tuple[int, dict]:
    sub = (body or {}).get("subscription")
    if not _valid(sub):
        return 400, {"error": "not a push subscription"}
    with _lock:
        subs = [s for s in _subs() if s["endpoint"] != sub["endpoint"]]
        subs.append({"endpoint": sub["endpoint"], "keys": {"p256dh": sub["keys"]["p256dh"], "auth": sub["keys"]["auth"]},
                     "device": str((body or {}).get("device") or "")[:60], "at": time.time()})
        common.write_json(SUBS, subs[-MAX_SUBS:])
    return 200, {"ok": True, "devices": len(subs[-MAX_SUBS:])}


def unsubscribe(body: dict) -> tuple[int, dict]:
    ep = str((body or {}).get("endpoint") or "")
    with _lock:
        subs = [s for s in _subs() if s["endpoint"] != ep]
        common.write_json(SUBS, subs)
    return 200, {"ok": True, "devices": len(subs)}


def status(endpoint: str = "") -> dict:
    subs = _subs()
    return {"available": available(), "devices": len(subs), "this_device": any(s["endpoint"] == endpoint for s in subs)}


def latest(limit: int = 5) -> list[dict]:
    return (common.read_json(LATEST, []) or [])[-limit:][::-1]


def send_all() -> int:
    """Empty push to every device; drops subscriptions the push service says are gone."""
    if not available():
        return 0
    from pywebpush import WebPushException, webpush
    v = _vapid()
    sent, gone = 0, []
    for s in _subs():
        try:
            webpush({"endpoint": s["endpoint"], "keys": s["keys"]}, data=None, vapid_private_key=v,
                    vapid_claims={"sub": CONTACT}, ttl=3600, headers={"Urgency": "high"}, timeout=10)
            sent += 1
        except WebPushException as exc:
            code = getattr(getattr(exc, "response", None), "status_code", None)
            if code in (404, 410):
                gone.append(s["endpoint"])
        except Exception:
            pass
    if gone:
        with _lock:
            common.write_json(SUBS, [s for s in _subs() if s["endpoint"] not in gone])
    return sent


def _title(sid: str, webui_state: Path) -> str:
    s = common.read_json(webui_state / "sessions" / f"{sid}.json", {}) or {}
    t = str(s.get("title") or "").strip()
    return t if t and t.lower() not in ("untitled", "new chat") else "Tabbie"


def event_for(rid: str, req: dict, webui_state: Path) -> dict | None:
    kind = EVENTS.get(str(req.get("status") or ""))
    sid = str(req.get("session_id") or "")
    if not kind or not sid:
        return None
    s = common.read_json(webui_state / "sessions" / f"{sid}.json", {}) or {}
    if s.get("archived"):
        return None
    if kind == "question":
        q = req.get("question") or {}
        qs = q.get("questions") if isinstance(q, dict) else None
        body = (qs[0].get("question") if qs and isinstance(qs[0], dict) else "") or "Tabbie has a question for you."
    elif kind == "plan":
        body = "A plan is ready for your approval."
    elif kind == "limit":
        body = "A usage limit stopped this job. Open the chat for the reset times."
    elif kind == "failed":
        body = "The job didn't finish. Open the chat for what happened."
    else:
        text = str((req.get("result") or {}).get("result") or "")
        lines = [ln.strip(" #*-") for ln in text.splitlines() if ln.strip() and not ln.startswith(("Route:", "ATTACH"))]
        body = (lines[0] if lines else "Done.")
    verb = {"done": "done", "failed": "didn't finish", "question": "needs your answer", "plan": "plan ready",
            "limit": "hit a limit"}[kind]
    return {"rid": rid, "kind": kind, "session": sid, "title": f"{_title(sid, webui_state)}: {verb}",
            "body": body[:160], "url": f"/session/{sid}", "at": time.time()}


class Watcher:
    """Polls recent requests; a status change into a finished / needs-you state queues one event and one
    push. Requests seen on start are baseline (no push for old news)."""

    def __init__(self, webui_state: Path, interval: float = 4.0, send=None):
        self.webui_state, self.interval, self.send = webui_state, interval, send or send_all
        self.seen: dict[str, str] = {}
        self.primed = False

    def _recent(self) -> list[Path]:
        rd = common.REQUESTS_DIR
        if not rd.exists():
            return []
        return sorted((d for d in rd.iterdir() if d.is_dir()), key=lambda d: d.name)[-40:]

    def tick(self) -> list[dict]:
        out = []
        for d in self._recent():
            req = common.read_json(d / "request.json", {}) or {}
            st = str(req.get("status") or "")
            if self.seen.get(d.name) == st:
                continue
            self.seen[d.name] = st
            if not self.primed:
                continue
            ev = event_for(d.name, req, self.webui_state)
            if ev:
                out.append(ev)
        self.primed = True
        if out:
            with _lock:
                common.write_json(LATEST, ((common.read_json(LATEST, []) or []) + out)[-KEEP:])
            if _subs():
                try:
                    self.send()
                except Exception:
                    pass
        return out

    def run(self):
        while True:
            try:
                self.tick()
            except Exception:
                pass
            time.sleep(self.interval)

    def start(self):
        threading.Thread(target=self.run, daemon=True, name="push-watcher").start()
