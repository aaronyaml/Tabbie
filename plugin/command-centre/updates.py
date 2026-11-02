"""Tabbie updates: check GitHub for a newer version, and download it only when the user says so.

check()    compares this install's commit with the repo's branch on GitHub (git ls-remote, plus the
           public compare API for the list of changes). Read-only; cached for 10 minutes.
apply()    fast-forwards this checkout to that commit (git fetch + merge --ff-only). Your own edits
           (e.g. config/router.yaml) stay; git refuses if the update would overwrite one, or if the
           history has diverged, so nothing is ever lost.
restart()  restarts what the update touched (gateway, WebUI, HTTPS proxy, sidecar), only when no
           worker or chat is running. WebUI chats run the router plugin inside the WebUI process, so
           plugin changes need the WebUI restarted too. WebUI extension files need just a page reload.
Settings: `updates:` in config/router.yaml (repo, branch, check). No tokens: the repo is public.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from .common import ROOT, STATE_DIR

DEFAULTS = {"repo": "aaronyaml/Tabbie", "branch": "main", "check": True}
CACHE_TTL = 600
STATE = STATE_DIR / "updates.json"
_SHA = re.compile(r"^[0-9a-f]{40}$")
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_BRANCH = re.compile(r"^[A-Za-z0-9_./-]{1,100}$")
_cache: dict = {"at": 0.0, "result": None}


def _read_block() -> dict:
    """The `updates:` block of config/router.yaml, stdlib only (the sidecar has no YAML library)."""
    out: dict = {}
    try:
        lines = (ROOT / "config" / "router.yaml").read_text().splitlines()
    except OSError:
        return out
    inside = False
    for line in lines:
        if re.match(r"^updates:\s*(#.*)?$", line):
            inside = True
            continue
        if inside:
            if line and not line[0].isspace() and not line.startswith("#"):
                break
            m = re.match(r"^\s+(repo|branch|check):\s*([^#]*?)\s*(#.*)?$", line)
            if m:
                v = m.group(2).strip().strip("'\"")
                out[m.group(1)] = (v.lower() != "false") if m.group(1) == "check" else v
    return out


def settings() -> dict:
    s = dict(DEFAULTS)
    s.update(_read_block())
    if not _REPO.match(str(s["repo"])) or not _BRANCH.match(str(s["branch"])) or ".." in str(s["branch"]):
        s.update(repo=DEFAULTS["repo"], branch=DEFAULTS["branch"])
    return s


def _git(*args: str, timeout: int = 30) -> str:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}  # a public repo never needs a login
    out = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True,
                         timeout=timeout, env=env)
    if out.returncode:
        msg = (out.stderr or out.stdout).strip()
        raise RuntimeError(msg or "git failed")
    return out.stdout.strip()


def _url(s: dict) -> str:
    return f"https://github.com/{s['repo']}.git"


def _changes(repo: str, base: str, head: str) -> dict | None:
    """GitHub's public compare API: how far ahead the remote is, and the commit titles."""
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/compare/{base}...{head}",
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": "tabbie-updates"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            j = json.load(r)
    except Exception:
        return None
    commits = [{"sha": c.get("sha", "")[:7],
                "title": str((c.get("commit") or {}).get("message", "")).splitlines()[0][:120] if c.get("commit") else ""}
               for c in (j.get("commits") or [])][-20:]
    return {"status": j.get("status"), "ahead_by": j.get("ahead_by", 0), "commits": commits[::-1]}


def check(force: bool = False) -> dict:
    """{available, local, remote, ahead_by, commits, note}. Never downloads the code."""
    if not force and _cache["result"] is not None and time.time() - _cache["at"] < CACHE_TTL:
        return _cache["result"]
    s = settings()
    res = {"available": False, "local": "", "remote": "", "ahead_by": 0, "commits": [], "note": "",
           "repo": s["repo"], "branch": s["branch"], "checked_at": int(time.time())}
    try:
        res["local"] = _git("rev-parse", "HEAD")
        line = _git("ls-remote", _url(s), f"refs/heads/{s['branch']}", timeout=20)
        remote = line.split()[0] if line else ""
        if not _SHA.match(remote):
            raise RuntimeError("branch not found on GitHub")
        res["remote"] = remote
    except Exception as exc:
        res["note"] = f"Couldn't check for updates ({(str(exc).strip().splitlines() or [''])[-1][:120]})"
        _cache.update(at=time.time(), result=res)
        return res
    if res["local"] != res["remote"]:
        diff = _changes(s["repo"], res["local"], res["remote"])
        if diff is None:  # API unreachable or rate-limited: GitHub has something else, say so plainly
            res["available"] = True
        elif diff["status"] in ("ahead",):
            res.update(available=True, ahead_by=diff["ahead_by"], commits=diff["commits"])
        elif diff["status"] == "identical":
            pass
        elif diff["status"] == "behind":
            res["note"] = "This Mac has changes that aren't on GitHub yet."
        else:  # diverged, or this commit isn't on GitHub at all
            res["note"] = "This copy has its own changes, so it can't update automatically."
    _cache.update(at=time.time(), result=res)
    return res


def _state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def _save_state(d: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=2))
    os.replace(tmp, STATE)


def _needs(files: list[str]) -> list[str]:
    need = set()
    for f in files:
        if f.startswith(("plugin/", "hmem/", "config/router.yaml")):
            need.update(("gateway", "webui", "sidecar"))
        if f == "services/Caddyfile":
            need.add("caddy")
        if f.startswith("webui-extension/"):
            need.add("reload")
        if f.startswith(("bin/setup", "services/")) and f != "services/Caddyfile":
            need.add("setup")
    return sorted(need)


def apply() -> dict:
    """Download the update the user accepted. Fast-forward only; local edits are never touched."""
    s = settings()
    info = check(force=True)
    if not info["available"]:
        return {"ok": False, "error": info["note"] or "Already up to date."}
    try:
        if _git("rev-parse", "--abbrev-ref", "HEAD") != s["branch"]:
            return {"ok": False, "error": f"Switch to the {s['branch']} branch to update."}
        before = _git("rev-parse", "HEAD")
        _git("fetch", "--quiet", _url(s), s["branch"], timeout=120)
        _git("merge", "--ff-only", "--quiet", "FETCH_HEAD", timeout=60)
        after = _git("rev-parse", "HEAD")
        files = [f for f in _git("diff", "--name-only", before, after).splitlines() if f]
    except Exception as exc:
        if "overwritten" in str(exc) or "local changes" in str(exc):
            edited = [f for f in _git("diff", "--name-only", "HEAD").splitlines() if f][:5]
            return {"ok": False, "error": "The update changes files you've edited on this Mac"
                    + (f" ({', '.join(edited)})" if edited else "") + ". Save a copy, undo the edits, then update."}
        last = (str(exc).strip().splitlines() or [""])[-1]
        return {"ok": False, "error": f"Update failed, nothing changed ({last[:160]})"}
    needs = _needs(files)
    installed = ensure_services()  # new services: nothing runs in them yet, so install right away
    changed = sorted({f[len("services/"):-len(".plist.template")] for f in files
                      if f.startswith("services/") and f.endswith(".plist.template")} - set(installed))
    _save_state({"from": before, "to": after, "at": int(time.time()), "needs": needs, "services": changed})
    _cache.update(at=0.0, result=None)
    return {"ok": True, "from": before[:7], "to": after[:7], "files": len(files), "needs": needs,
            "installed": installed}


def pending_restart() -> list[str]:
    return [n for n in (_state().get("needs") or []) if n in ("gateway", "webui", "sidecar", "caddy")] \
        + (["services"] if _state().get("services") else [])


# ---------------------------------------------------------------- background services
# bin/setup installs services/*.plist.template as LaunchAgents. An update can add a new service (the
# cc-sidecar arrived after some installs) or change one, so updates install what's missing and
# reinstall what changed. Nothing happens on a Mac where setup never installed services.
SERVICES = ROOT / "services"
AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, timeout=30)


def _loaded(label: str) -> bool:
    return _launchctl("print", f"gui/{os.getuid()}/{label}").returncode == 0


def _render(template: Path) -> str | None:
    text = template.read_text()
    if "__CADDY__" in text:
        caddy = shutil.which("caddy") or str(Path.home() / ".local/bin/caddy")
        if not Path(caddy).exists():
            return None  # setup installs Caddy first; leave this service to it
        text = text.replace("__CADDY__", caddy)
    return text.replace("__HOME__", str(Path.home()))


def _install(label: str, text: str) -> bool:
    dest = AGENTS_DIR / f"{label}.plist"
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    dest.write_text(text)
    _launchctl("bootout", f"gui/{os.getuid()}/{label}")
    return _launchctl("bootstrap", f"gui/{os.getuid()}", str(dest)).returncode == 0


def ensure_services() -> list[str]:
    """Install any service from services/ that isn't loaded yet. Safe to call any time: a missing
    service has nothing running in it. Returns the labels it installed."""
    if sys.platform != "darwin" or os.environ.get("TABBIE_NO_SERVICES") or not _loaded("ai.hermes.webui"):
        return []
    import fcntl
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_DIR / "services.lock", "w") as lock:
        try:  # several Hermes processes start together; one installs, the rest skip
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return []
        done = []
        for t in sorted(SERVICES.glob("*.plist.template")):
            label = t.name[: -len(".plist.template")]
            if _loaded(label):
                continue
            text = _render(t)
            if text and _install(label, text):
                done.append(label)
        return done


def _reinstall_changed(labels: list[str]) -> list[str]:
    """Rewrite and reload services whose template an update changed (idle only, from restart()).
    Detached, so restarting the sidecar or WebUI from inside it still finishes."""
    cmds, done = [], []
    for label in labels:
        t = SERVICES / f"{label}.plist.template"
        text = _render(t) if t.exists() else None
        if not text:
            continue
        dest = AGENTS_DIR / f"{label}.plist"
        AGENTS_DIR.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)
        cmds.append(f"launchctl bootout gui/{os.getuid()}/{label}; launchctl bootstrap gui/{os.getuid()} '{dest}'")
        done.append(label)
    if cmds:
        subprocess.Popen(["/bin/sh", "-c", "sleep 2; " + "; ".join(cmds)], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return done


def busy() -> bool:
    """True while a worker is running (planning or executing), so a restart would cut it off."""
    rd = STATE_DIR / "requests"
    if rd.exists():
        for d in sorted(rd.iterdir())[-40:]:
            try:
                st = json.loads((d / "request.json").read_text()).get("status")
            except (OSError, ValueError):
                continue
            if st in ("executing", "planning"):
                return True
    out = subprocess.run(["pgrep", "-f", r"claude -p|codex exec|remotion render"], capture_output=True, text=True)
    return bool(out.stdout.strip()) or _chatting()


def _chatting() -> bool:
    """A WebUI reply is streaming (the hub answering by itself leaves no request record)."""
    port = os.environ.get("HERMES_WEBUI_PORT") or "8787"
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as r:
            h = json.loads(r.read() or b"{}")
        return bool(h.get("active_streams") or h.get("active_runs"))
    except (OSError, ValueError):
        return False


def restart(after_reply=None) -> dict:
    """Restart what the last update touched. The sidecar goes last (after_reply), so the browser
    gets its answer first; launchd starts it again."""
    needs = pending_restart()
    if not needs:
        return {"ok": True, "restarted": []}
    if busy():
        return {"ok": False, "error": "A request is running. Try again when it finishes."}
    uid = os.getuid()
    done = []
    if "gateway" in needs:
        subprocess.run(["launchctl", "kickstart", "-k", f"gui/{uid}/ai.hermes.gateway"], capture_output=True)
        done.append("gateway")
    if "caddy" in needs:
        caddy = shutil.which("caddy") or str(Path.home() / ".local/bin/caddy")
        subprocess.run([caddy, "reload", "--config", str(ROOT / "services" / "Caddyfile")], capture_output=True)
        done.append("caddy")
    if "webui" in needs:
        # The browser's restart call comes through the WebUI, so let it answer first.
        subprocess.Popen(["/bin/sh", "-c", f"sleep 2; launchctl kickstart -k gui/{uid}/ai.hermes.webui"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        done.append("webui")
    st = _state()
    if st.get("services") and sys.platform == "darwin" and not os.environ.get("TABBIE_NO_SERVICES"):
        done += _reinstall_changed(st["services"])
    st["needs"] = [n for n in st.get("needs", []) if n not in ("gateway", "webui", "caddy", "sidecar")]
    st.pop("services", None)
    _save_state(st)
    if "sidecar" in needs and after_reply:
        after_reply()
        done.append("sidecar")
    return {"ok": True, "restarted": done}


# ---------------------------------------------------------------- restart when idle
# Downloading an update never touches running work (Motion's studio folder is git-ignored and
# workers keep their files open). Only the restart could cut something off, so it waits: for any
# request, worker or Motion render to finish, then IDLE_GRACE seconds more so the hub can finish
# writing its reply. The wish survives a sidecar restart (state file); the sidecar resumes it.
IDLE_GRACE = 90
POLL = 15
_waiter: dict = {"thread": None}


def waiting() -> bool:
    return bool(_state().get("restart_when_idle")) and bool(pending_restart())


def restart_when_idle(after_reply=None) -> dict:
    """Restart now when nothing is running, else as soon as Tabbie has been idle for IDLE_GRACE s."""
    if not pending_restart():
        return {"ok": True, "restarted": []}
    if not busy():
        return restart(after_reply)
    st = _state()
    st["restart_when_idle"] = True
    _save_state(st)
    resume(after_reply)
    return {"ok": True, "waiting": True}


def resume(after_reply=None) -> None:
    """Start the idle watcher if a deferred restart is pending (sidecar start, or a new request)."""
    t = _waiter["thread"]
    if not waiting() or (t and t.is_alive()):
        return
    import threading

    def watch():
        quiet = 0
        while waiting():
            quiet = 0 if busy() else quiet + POLL
            if quiet >= IDLE_GRACE:
                st = _state()
                st.pop("restart_when_idle", None)
                _save_state(st)
                restart(after_reply)
                return
            time.sleep(POLL)

    _waiter["thread"] = threading.Thread(target=watch, daemon=True, name="tabbie-update-restart")
    _waiter["thread"].start()

