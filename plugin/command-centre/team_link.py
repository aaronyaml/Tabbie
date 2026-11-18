"""Settings → Team: connect this Tabbie to a team relay (any server speaking protocol tt1, see docs/team/).

Thin wrapper over bin/tt (team/tt.py), which holds the keys and does all the crypto. This module only runs it
and passes plain JSON back to the WebUI: names, fingerprints, states. Never keys or tokens.

Approving a device: the WebUI shows who is waiting but never the code. You type the code the device shows,
so approving proves you looked at that device.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] if (Path(__file__).resolve().parents[2] / "team").is_dir() else Path.home() / "hermes"
CODE = re.compile(r"^[0-9A-Za-z]{4}-?[0-9A-Za-z]{4}-?[0-9A-Za-z]{4}$")
_CACHE: dict = {}
TTL = 8


def tt_path() -> str | None:
    p = os.environ.get("TABBIE_TEAM_TT") or str(ROOT / "bin" / "tt")
    return p if os.access(p, os.X_OK) else None


def _run(args: list[str], timeout: int = 40) -> tuple[int, str, str]:
    tt = tt_path()
    if not tt:
        return 127, "", "Tabbie Team isn't installed (bin/tt missing)."
    try:
        r = subprocess.run([tt, *args], capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return 124, "", "The team relay took too long to answer. Try again."
    except OSError as e:
        return 126, "", f"Couldn't run tt ({type(e).__name__})."
    return r.returncode, r.stdout, r.stderr


def warm():
    """Run tt once in the background when the helper starts, so its libraries are fetched and cached before anyone
    presses Join (on a new Mac that first run can take a minute)."""
    import threading

    threading.Thread(target=lambda: _run(["info"], timeout=300), daemon=True).start()


def _err(stderr: str) -> str:
    text = (stderr or "").strip()
    if re.search(r"Failed to build|No solution found|Failed to download|building source distributions is disabled|"
                 r"no wheels|maturin failed|Could not find directory of OpenSSL", text, re.I):
        # uv couldn't set up Team's components: the last line would be one of uv's hints, which means nothing to anyone
        return ("Tabbie couldn't install Team's components on this Mac. Update Tabbie (Settings → Updates) and try again; "
                "if it keeps happening, send Tabbie's support logs.")
    lines = [l for l in text.splitlines() if l.strip() and not l.lstrip().startswith(("hint:", "help:", "warning:"))]
    tt_lines = [l for l in lines if l.startswith("tt:")]
    line = (tt_lines or lines)[-1:] or ["Something went wrong."]
    return re.sub(r"^tt:\s*", "", line[0].strip())[:300]


def _json(args: list[str], timeout: int = 40) -> tuple[int, dict]:
    code, out, err = _run(args, timeout)
    if code != 0:
        return (400 if code == 1 else 502), {"ok": False, "error": _err(err)}
    try:
        return 200, json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return 502, {"ok": False, "error": "tt gave an answer Tabbie couldn't read."}


def _projects() -> list[dict]:
    from hmem.paths import webui_state  # noqa: PLC0415

    try:
        rows = json.loads((webui_state() / "projects.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if isinstance(rows, dict):
        rows = rows.get("projects", [])
    return sorted({str(p.get("name")) for p in rows if isinstance(p, dict) and p.get("name")}, key=str.lower)


def status(force: bool = False) -> tuple[int, dict]:
    """Everything Settings → Team shows, plus who is waiting for approval (names only)."""
    hit = _CACHE.get("status")
    if hit and not force and time.time() - hit[0] < TTL:
        return 200, hit[1]
    if not tt_path():
        return 200, {"installed": False}
    if not _config().get("relay"):  # no team: answer without starting tt (the WebUI asks every 20 s)
        d = {"installed": True, "relay": None, "name": None, "projects": [], "linked": False, "pairing": None,
             "owner": None, "mac": None, "devices": [], "macs": [], "all_projects": _projects(), "pending": []}
        _CACHE["status"] = (time.time(), d)
        return 200, d
    code, d = _json(["info"])
    if code != 200:
        return code, d
    d["installed"] = True
    d["all_projects"] = _projects()
    d["pending"] = []
    if d.get("linked"):
        c2, p = _json(["pending"])
        if c2 == 200:
            d["pending"] = p.get("pending", [])
        else:
            d["relay_error"] = p.get("error")
    _CACHE["status"] = (time.time(), d)
    return 200, d


def _home() -> Path:
    return Path(os.environ.get("TT_HOME") or Path.home() / ".hermes" / "team")


def _config() -> dict:
    try:
        c = json.loads((_home() / "config.json").read_text(encoding="utf-8"))
        return c if isinstance(c, dict) else {}
    except (OSError, ValueError):
        return {}


def done() -> bool:
    """For setup news: a relay is set and this Mac is linked."""
    return bool(_config().get("relay")) and (_home() / "team.json").exists()


def post(path: str, body: dict) -> tuple[int, dict]:
    _CACHE.clear()
    act = path.rsplit("/", 1)[-1]
    if act == "connect":
        url = str(body.get("url") or "").strip()
        if not url or len(url) > 200 or any(c.isspace() for c in url):
            return 400, {"ok": False, "error": "Type the team relay's address, like https://team.example.com"}
        code, r = _json(["connect", url, "--json"])
    elif act == "pair":
        name = re.sub(r"[^\w .'-]", "", str(body.get("name") or ""))[:40].strip()
        code, r = _json(["pair", "--start", "--json", *(["--name", name] if name else [])])
    elif act == "pair-check":
        code, r = _json(["pair", "--check", "--json"])
    elif act == "share":
        proj = str(body.get("project") or "").strip()
        if not proj or proj not in _projects():
            return 400, {"ok": False, "error": "Pick one of your projects."}
        code, r = _json(["share" if body.get("on") else "unshare", proj, "--json"])
    elif act == "approve":
        c = str(body.get("code") or "").strip().upper().replace(" ", "")
        if not CODE.match(c):
            return 400, {"ok": False, "error": "Type the 12-character code the device shows, like ABCD-EFGH-JKLM."}
        if "-" not in c:
            c = f"{c[:4]}-{c[4:8]}-{c[8:]}"
        code, r = _json(["approve", c, "--json"])
        if code != 200 and "No waiting device" in r.get("error", ""):
            r["error"] = "That code doesn't match any device waiting. Check the code on the device and try again."
    elif act == "invite":
        who = str(body.get("for") or "").strip().lower()
        if not re.fullmatch(r"[a-z0-9._-]{1,40}", who):
            return 400, {"ok": False, "error": "Type their team name, like leo."}
        code, r = _json(["invite", who, "--json"])
    elif act == "join":
        c = "".join(str(body.get("code") or "").split())
        if not c or len(c) > 600:
            return 400, {"ok": False, "error": "Paste the whole invite code. It starts with TT1-."}
        code, r = _json(["join", c, "--json"], timeout=180)  # a new Mac's first run also fetches tt's libraries
    elif act == "remove":  # a teammate leaves: their devices are revoked and the team key changes
        who = str(body.get("who") or "").strip().lower()
        if not re.fullmatch(r"[a-z][a-z0-9-]{1,19}", who):
            return 400, {"ok": False, "error": "Pick a teammate to remove."}
        code, r = _json(["remove", who, "--json"], timeout=60)
    elif act == "remove-device":  # one of this person's own browsers or phones
        c = str(body.get("fingerprint") or "").strip().upper()
        if not CODE.match(c.replace("-", "")) and not CODE.match(c):
            return 400, {"ok": False, "error": "Pick a device to remove."}
        code, r = _json(["revoke", c, "--json"], timeout=60)
    elif act == "disconnect":
        code, r = _json(["disconnect", "--json"])
    else:
        return 404, {"ok": False, "error": "unknown team action"}
    if code != 200:
        return code, r
    st = status(force=True)[1]
    return 200, {"ok": True, "result": r, **st}
