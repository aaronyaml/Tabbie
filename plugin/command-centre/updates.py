"""Tabbie updates: check GitHub for a newer version, and download it only when the user says so.

check()    compares this install's commit with the repo's branch on GitHub (git ls-remote, plus the
           public compare API for the list of changes). Read-only; cached for 10 minutes.
apply()    fetches that commit, unpacks it in state/update-stage and runs its preflight there (installs
           every package the new code needs, ready-made builds only; checks the Caddyfile), and only
           then fast-forwards this checkout (git merge --ff-only). A failed preflight changes nothing.
           Your own edits (e.g. config/router.yaml) stay; git refuses if the update would overwrite
           one, or if the history has diverged, so nothing is ever lost.
restart()  restarts what the update touched (gateway, WebUI, HTTPS proxy, sidecar), only when no
           worker or chat is running. WebUI chats run the router plugin inside the WebUI process, so
           plugin changes need the WebUI restarted too. WebUI extension files need just a page reload.
verify()   after a restart, waits up to ~90 s for every restarted service to answer; if one doesn't,
           rollback() returns to the previous version and restarts again. bin/tabbie-update runs this
           outside the helper (and its own background service), so a broken helper can't block a fix.
Settings: `updates:` in config/router.yaml (repo, branch, check). No tokens: the repo is public.
"""
from __future__ import annotations

import contextlib
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
CACHE_TTL = 240  # the page checks every 5 min; a new version shows within ~5
STATE = STATE_DIR / "updates.json"
_SHA = re.compile(r"^[0-9a-f]{40}$")
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_BRANCH = re.compile(r"^[A-Za-z0-9_./-]{1,100}$")
_cache: dict = {"at": 0.0, "result": None}


def _uid() -> int:
    return os.getuid() if hasattr(os, "getuid") else 0  # launchd only exists on macOS


def _read_block() -> dict:
    """The `updates:` block of config/router.yaml, stdlib only (the sidecar has no YAML library)."""
    out: dict = {}
    try:
        lines = (ROOT / "config" / "router.yaml").read_text(encoding="utf-8").splitlines()
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
    out = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
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
            res["note"] = "This computer has changes that aren't on GitHub yet."
        else:  # diverged, or this commit isn't on GitHub at all
            res["note"] = "This copy has its own changes, so it can't update automatically."
    _cache.update(at=time.time(), result=res)
    return res


def _state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(d: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=2), encoding="utf-8")
    os.replace(tmp, STATE)


# Plugin modules only the helper (cc-sidecar) loads: a change to just these restarts the helper (about a
# second), not the WebUI and gateway (about 15 s). Anything else under plugin/ is treated as WebUI code.
SIDECAR_ONLY = {f"plugin/command-centre/{m}.py" for m in ("sidecar", "connections", "hub_setup", "linkicons", "team_link", "usage_insights",
                                                              "upstream")}


def _needs(files: list[str]) -> list[str]:
    need = set()
    for f in files:
        if f in SIDECAR_ONLY:
            need.add("sidecar")
        elif f.startswith(("plugin/", "hmem/", "config/router.yaml")):
            need.update(("gateway", "webui", "sidecar"))
        if f.startswith("team/") or f == "bin/tt":
            need.add("team")
        if f == "bin/cc-sidecar":  # its launcher decides which libraries it starts with
            need.add("sidecar")
        if f == "bin/hermes-webui-run":
            need.add("webui")
        if f == "services/Caddyfile":
            need.add("caddy")
        if f.startswith("webui-patches/") or f == "config/upstream.lock":  # applied to hermes-webui/ on update
            need.add("webui")
        if f.startswith("webui-extension/"):
            need.add("reload")
        if f.startswith(("bin/setup", "services/")) and f != "services/Caddyfile":
            need.add("setup")
    return sorted(need)


def _overwrite_clashes(head: str) -> dict:
    """Edited or new local files that the update also changes: copy them to backups/ and undo them, so
    the update can land. Edits to files the update doesn't touch stay as they are."""
    theirs = set(filter(None, _git("diff", "--name-only", head, "FETCH_HEAD").splitlines()))
    edited = set(filter(None, _git("diff", "--name-only", "HEAD").splitlines()))
    new = set(filter(None, _git("ls-files", "--others", "--exclude-standard").splitlines()))
    clash = sorted((edited | new) & theirs)
    if not clash:
        return {}
    dest = ROOT / "backups" / f"update-{time.strftime('%Y%m%d-%H%M%S')}"
    for f in clash:
        src = ROOT / f
        if src.is_file():
            (dest / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest / f)
    tracked = [f for f in clash if f in edited]
    if tracked:
        _git("checkout", "HEAD", "--", *tracked)
    for f in clash:
        if f in new:
            (ROOT / f).unlink(missing_ok=True)
    return {"dir": str(dest.relative_to(ROOT)), "files": clash}


def apply() -> dict:
    """Download the update the user accepted. Fast-forward only. Local edits to files the update changes
    are copied to backups/ first and then overwritten; other edits are left alone."""
    s = settings()
    info = check(force=True)
    if not info["available"]:
        return {"ok": False, "error": info["note"] or "Already up to date."}
    try:
        if _git("rev-parse", "--abbrev-ref", "HEAD") != s["branch"]:
            return {"ok": False, "error": f"Switch to the {s['branch']} branch to update."}
        before = _git("rev-parse", "HEAD")
        _git("fetch", "--quiet", _url(s), s["branch"], timeout=120)
        target = _git("rev-parse", "FETCH_HEAD")
    except Exception as exc:
        last = (str(exc).strip().splitlines() or [""])[-1]
        return {"ok": False, "error": f"Update failed, nothing changed ({last[:160]})"}
    pf = preflight(target)
    if not pf["ok"]:
        _save_state({**_state(), "failed": {"to": target, "step": pf["step"], "detail": pf["detail"], "at": int(time.time()),
                                            "rolled_back": False}})
        return {"ok": False, "error": f"Update not installed, nothing changed: {pf['step']} failed ({pf['detail'][:160]})",
                "preflight": pf}
    try:
        saved = _overwrite_clashes(before)
        _git("merge", "--ff-only", "--quiet", "FETCH_HEAD", timeout=60)
        after = _git("rev-parse", "HEAD")
        shutil.rmtree(ROOT / "state" / "update-stage", ignore_errors=True)  # checked and switched to: not needed now
        files = [f for f in _git("diff", "--name-only", before, after).splitlines() if f]
    except Exception as exc:
        last = (str(exc).strip().splitlines() or [""])[-1]
        return {"ok": False, "error": f"Update failed, nothing changed ({last[:160]})"}
    needs = _needs(files)
    upstream_note = _sync_upstream(files)
    if upstream_note:
        pf.setdefault("warnings", []).append(upstream_note)
    installed = ensure_services()  # new services: nothing runs in them yet, so install right away
    changed = sorted({f[len("services/"):-len(".plist.template")] for f in files
                      if f.startswith("services/") and f.endswith(".plist.template")} - set(installed))
    if sys.platform == "win32" and any(f in ("hmem/winsvc.py", "bin/tabbie-mdns.py") for f in files):
        changed = ["supervisor"]  # the service list lives in code there
    _save_state({"from": before, "to": after, "at": int(time.time()), "needs": needs, "services": changed,
                 "warnings": pf.get("warnings") or []})
    _cache.update(at=0.0, result=None)
    return {"ok": True, "from": before[:7], "to": after[:7], "files": len(files), "needs": needs,
            "installed": installed, "backed_up": saved, "warnings": pf.get("warnings") or []}


def _sync_upstream(files: list[str]) -> str:
    """After switching: move the WebUI to the tested version if the lock moved, and (re)apply Tabbie's WebUI
    patches. They only take effect when the WebUI restarts. Returns a warning, or ''."""
    if sys.platform == "win32" or not (ROOT / "hermes-webui" / ".git").exists():
        return ""
    cmd = "upstream" if "config/upstream.lock" in files else "webui-patches"
    tool = ROOT / "bin" / cmd
    if not tool.exists():
        return ""
    ok, out = _run_check([str(tool), *(["sync"] if cmd == "upstream" else [])], timeout=300)
    bad = [x for x in out.splitlines() if "CONFLICT" in x or "not moving" in x or "failed" in x]
    if bad:
        return "Some of Tabbie's WebUI changes no longer fit this WebUI version: " + "; ".join(bad)[:200]
    return ""


# ---------------------------------------------------------------- preflight (before switching code)
# "Install around the time the code needs it": everything the new version needs is installed while the
# old version is still running, from the new version's own launchers. Only if that works does the code
# switch over. uv caches what it installs, so the services start straight away afterwards.
def _stage(sha: str) -> Path:
    """Unpack commit `sha` (already fetched) into state/update-stage/<sha> and return that folder."""
    stage = ROOT / "state" / "update-stage"
    dest = stage / sha[:12]
    if (dest / ".complete").exists():
        return dest
    shutil.rmtree(stage, ignore_errors=True)  # one stage at a time: old ones are never needed again
    dest.mkdir(parents=True)
    arch = subprocess.run(["git", "-C", str(ROOT), "archive", "--format=tar", sha], capture_output=True, timeout=120)
    if arch.returncode:
        raise RuntimeError((arch.stderr or b"git archive failed").decode("utf-8", "replace").strip()[-200:])
    import io
    import tarfile
    with tarfile.open(fileobj=io.BytesIO(arch.stdout)) as tf:
        for m in tf.getmembers():  # our own commit, but never write outside the stage folder
            if m.name.startswith(("/", "..")) or "/../" in m.name or m.issym() or m.islnk():
                continue
            tf.extract(m, dest, **({"filter": "data"} if hasattr(tarfile, "data_filter") else {}))
    (dest / ".complete").touch()
    return dest


def _run_check(argv: list[str], timeout: int = 900) -> tuple[bool, str]:
    try:
        r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                           stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return False, "took too long"
    except OSError as exc:
        return False, str(exc)
    out = "\n".join(x for x in (r.stdout + "\n" + r.stderr).splitlines() if x.strip())
    return r.returncode == 0, out[-400:]


def preflight(sha: str) -> dict:
    """Run the new version's checks before switching to it. {ok, step, detail, warnings, steps}."""
    warnings, steps = [], []
    if os.environ.get("TABBIE_SKIP_PREFLIGHT"):
        return {"ok": True, "step": "", "detail": "", "warnings": [], "steps": []}
    try:
        stage = _stage(sha)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "step": "unpacking the new version", "detail": str(exc), "warnings": [], "steps": []}

    def fail(step, detail):
        return {"ok": False, "step": step, "detail": detail, "warnings": warnings, "steps": steps}

    # 1. every Python file parses (a syntax error would stop a service starting)
    import ast
    for p in stage.rglob("*.py"):
        if "node_modules" in p.parts:
            continue
        try:
            ast.parse(p.read_text(encoding="utf-8"), str(p))
        except SyntaxError as exc:
            return fail("checking the new code", f"{p.relative_to(stage)} line {exc.lineno}: {exc.msg}")
        except (OSError, UnicodeDecodeError):
            continue
    steps.append("code")
    if sys.platform != "win32":
        # 2. the packages each service needs, installed now (ready-made builds only) by its own launcher
        for name, launcher, required in (("the helper", "cc-sidecar", True), ("Team", "tt", _team_installed())):
            exe = stage / "bin" / launcher
            if not exe.exists() or "--preflight" not in exe.read_text(encoding="utf-8", errors="replace"):
                continue  # (a launcher without a preflight mode would start the service itself)
            os.chmod(exe, 0o755)
            ok, out = _run_check([str(exe), "--preflight"])
            if ok:
                steps.append(launcher)
                if "optional:" in out:
                    warnings.append(out.split("optional:", 1)[1].strip().splitlines()[0][:200])
            elif required:
                return fail(f"installing {name}'s components", out)
            else:
                warnings.append(f"{name}'s components couldn't be installed yet ({out[-120:]})")
        # 3. the HTTPS proxy's config
        caddy = shutil.which("caddy") or str(Path.home() / ".local/bin/caddy")
        cf = stage / "services" / "Caddyfile"
        if cf.exists() and Path(caddy).exists() and _caddyfile_changed(sha):
            ok, out = _run_check([caddy, "validate", "--config", str(cf), "--adapter", "caddyfile"], timeout=60)
            if not ok:
                return fail("checking the HTTPS settings (Caddyfile)", out)
            steps.append("caddy")
    return {"ok": True, "step": "", "detail": "", "warnings": warnings, "steps": steps}


def _caddyfile_changed(sha: str) -> bool:
    try:
        return bool(_git("diff", "--name-only", "HEAD", sha, "--", "services/Caddyfile"))
    except Exception:  # noqa: BLE001
        return True


def _team_installed() -> bool:
    return sys.platform == "darwin" and _loaded("ai.hermes.team") and (Path.home() / ".hermes" / "team" / "mac.json").exists()


def pending_restart() -> list[str]:
    return [n for n in (_state().get("needs") or []) if n in ("gateway", "webui", "sidecar", "caddy", "team")] \
        + (["services"] if _state().get("services") else [])


# ---------------------------------------------------------------- background services
# bin/setup installs services/*.plist.template as LaunchAgents. An update can add a new service (the
# cc-sidecar arrived after some installs) or change one, so updates install what's missing and
# reinstall what changed. Nothing happens on a Mac where setup never installed services.
SERVICES = ROOT / "services"
AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)


def _loaded(label: str) -> bool:
    return _launchctl("print", f"gui/{_uid()}/{label}").returncode == 0


def _render(template: Path) -> str | None:
    text = template.read_text(encoding="utf-8")
    if "__CADDY__" in text:
        caddy = shutil.which("caddy") or str(Path.home() / ".local/bin/caddy")
        if not Path(caddy).exists():
            return None  # setup installs Caddy first; leave this service to it
        text = text.replace("__CADDY__", caddy)
    return text.replace("__HOME__", str(Path.home()))


def _install(label: str, text: str) -> bool:
    dest = AGENTS_DIR / f"{label}.plist"
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    _launchctl("bootout", f"gui/{_uid()}/{label}")
    return _launchctl("bootstrap", f"gui/{_uid()}", str(dest)).returncode == 0


def _winsvc():
    from .procutil import procutil  # noqa: F401  (puts ~/hermes on sys.path)
    from hmem import winsvc
    return winsvc


def ensure_services() -> list[str]:
    """Install any service from services/ that isn't loaded yet. Safe to call any time: a missing
    service has nothing running in it. Returns the labels it installed. On Windows one supervisor
    runs them all (hmem/winsvc.py): start it if setup installed it and it isn't running."""
    if sys.platform == "win32":
        w = _winsvc()
        return ["supervisor"] if not os.environ.get("TABBIE_NO_SERVICES") and w.installed() and w.start() else []
    if sys.platform != "darwin" or os.environ.get("TABBIE_NO_SERVICES") or not _loaded("ai.hermes.webui"):
        return []
    from .flockcompat import fcntl
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_DIR / "services.lock", "w", encoding="utf-8") as lock:
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
        dest.write_text(text, encoding="utf-8")
        cmds.append(f"launchctl bootout gui/{_uid()}/{label}; launchctl bootstrap gui/{_uid()} '{dest}'")
        done.append(label)
    if cmds:
        subprocess.Popen(["/bin/sh", "-c", "sleep 2; " + "; ".join(cmds)], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return done


def _workers_alive() -> bool:
    from .procutil import procutil
    pids = set(procutil.find(r"claude(\.exe|\.cmd|\.js)?\"? -p|codex(\.exe|\.cmd|\.js)?\"? exec|remotion render"))
    # Team mail's short background calls (summaries, triage: no tools, --safe-mode) aren't work a restart cuts off
    pids -= set(procutil.find(r"claude.* -p .*--safe-mode .*--strict-mcp-config"))
    return bool(pids)


INTERRUPTED = "Interrupted: Tabbie restarted while this was running. Send it again to retry."


def reap_orphans(min_age: float = 120.0) -> list[str]:
    """Settle requests left "executing"/"planning" by a restart or crash: their worker is gone, so they
    would show as running forever and block every update restart (busy() would stay true). Only when
    no worker process exists at all, and only records untouched for `min_age` seconds. Returns the ids."""
    rd = STATE_DIR / "requests"
    if not rd.exists() or _workers_alive():
        return []
    now, done = time.time(), []
    for d in sorted(x for x in rd.iterdir() if x.is_dir())[-60:]:
        rf = d / "request.json"
        try:
            req = json.loads(rf.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if req.get("status") not in ("executing", "planning"):
            continue
        newest = max((f.stat().st_mtime for f in d.iterdir() if f.is_file()), default=0)
        if now - newest < min_age:
            continue
        req["status"] = "failed"
        req["result"] = {**(req.get("result") or {}), "ok": False, "error": INTERRUPTED}
        live = d / "live.json"
        if live.exists():  # its worker is gone: Stop / Send now must not target its old pid
            _write(live, {"open": False, "pid": None, "at": now})
        req["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        _write(rf, req)
        af = d / "agents.json"
        try:
            ag = json.loads(af.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            ag = None
        if ag:
            n = 0
            for a in ag.get("agents") or []:
                if a.get("status") in ("running", "waiting"):
                    a.update(status="failed", step="Interrupted by a restart", ended=now,
                             duration_s=round(now - (a.get("started") or now), 1))
                    n += 1
            sm = ag.get("summary") or {}
            if sm:
                sm["running"] = 0
                sm["waiting"] = False
                sm["failed"] = (sm.get("failed") or 0) + n
            ag["updated"] = now
            _write(af, ag)
        done.append(d.name)
    return done


def prune_old_attachments(days: float | None = None, *, now: float | None = None) -> list[str]:
    """Drop the attachment copies of settled requests older than `days` (retention.attachments_days, 30).
    They are byte copies of files the user still has; replies, images and agent blocks in old chats stay.
    Runs at most once a day. Returns the request ids it cleaned."""
    now = now or time.time()
    stamp = STATE_DIR / ".pruned-at"
    if days is None:
        try:
            if now - stamp.stat().st_mtime < 86400:
                return []
        except OSError:
            pass
        try:
            from .common import load_config
            days = float(((load_config().get("retention") or {}).get("attachments_days")) or 30)
        except Exception:
            days = 30.0
    rd, done = STATE_DIR / "requests", []
    if rd.exists():
        for d in (x for x in rd.iterdir() if x.is_dir()):
            att = d / "attachments"
            try:
                if not att.is_dir() or now - (d / "request.json").stat().st_mtime < days * 86400:
                    continue
                if json.loads((d / "request.json").read_text(encoding="utf-8")).get("status") in ("executing", "planning"):
                    continue
            except (OSError, ValueError):
                continue
            shutil.rmtree(att, onerror=lambda fn, path, _e: (os.chmod(path, 0o700), fn(path)))
            done.append(d.name)
    with contextlib.suppress(OSError):
        stamp.touch()
    return done


def _write(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def busy() -> bool:
    """True while a worker is running (planning or executing), so a restart would cut it off."""
    try:
        reap_orphans()
        prune_old_attachments()  # once a day at most
    except OSError:
        pass
    rd = STATE_DIR / "requests"
    if rd.exists():
        for d in sorted(rd.iterdir())[-40:]:
            try:
                st = json.loads((d / "request.json").read_text(encoding="utf-8")).get("status")
            except (OSError, ValueError):
                continue
            if st in ("executing", "planning"):
                return True
    return _workers_alive() or _chatting()


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
    uid = _uid()
    win = sys.platform == "win32"
    done = []
    if "gateway" in needs:
        if win:
            hermes = shutil.which("hermes")
            if hermes:
                subprocess.run([hermes, "gateway", "restart"], capture_output=True, timeout=120)
        else:
            subprocess.run(["launchctl", "kickstart", "-k", f"gui/{uid}/ai.hermes.gateway"], capture_output=True)
        done.append("gateway")
    if "caddy" in needs:
        caddy = shutil.which("caddy") or str(Path.home() / ".local/bin/caddy")
        subprocess.run([caddy, "reload", "--config", str(ROOT / "services" / "Caddyfile")], capture_output=True)
        done.append("caddy")
    if "webui" in needs and win:
        _winsvc().request_restart(["webui"], delay=2)  # the supervisor restarts it once we've answered
        done.append("webui")
    elif "webui" in needs:
        # The browser's restart call comes through the WebUI, so let it answer first.
        subprocess.Popen(["/bin/sh", "-c", f"sleep 2; launchctl kickstart -k gui/{uid}/ai.hermes.webui"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        done.append("webui")
    if "team" in needs and not win and _loaded("ai.hermes.team"):
        subprocess.run(["launchctl", "kickstart", "-k", f"gui/{uid}/ai.hermes.team"], capture_output=True)
        done.append("team")
    st = _state()
    if st.get("services") and not os.environ.get("TABBIE_NO_SERVICES"):
        if sys.platform == "darwin":
            done += _reinstall_changed(st["services"])
        elif win and _winsvc().running():
            _winsvc().request_restart(["supervisor"], delay=3)
            done.append("supervisor")
    st["needs"] = [n for n in st.get("needs", []) if n not in ("gateway", "webui", "caddy", "sidecar", "team")]
    st.pop("services", None)
    _save_state(st)
    if "sidecar" in needs and after_reply:
        after_reply()
        done.append("sidecar")
    if after_reply is not _kick_sidecar:  # from the helper: a separate process checks everything came back
        schedule_verify(done)
    return {"ok": True, "restarted": done}


# ---------------------------------------------------------------- restart when idle
# Downloading an update never touches running work (Motion's studio folder is git-ignored and
# workers keep their files open). Only the restart could cut something off, so it waits: for any
# request, worker or Motion render to finish, then IDLE_GRACE seconds more so the hub can finish
# writing its reply. The wish survives a sidecar restart (state file); the sidecar resumes it.
IDLE_GRACE = 30
POLL = 5
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



# ---------------------------------------------------------------- health check and rollback
# After a restart, every service it touched must answer within VERIFY_S. If one doesn't, go back to the
# version before the update (git reset --keep: local edits stay), restart again and record what failed,
# so Settings → Updates can say so. bin/tabbie-update runs this in its own process: the helper restarts
# itself during an update, so it can't be the one checking it came back.
VERIFY_S = 120
_LABELS = {"gateway": "ai.hermes.gateway", "webui": "ai.hermes.webui", "sidecar": "ai.hermes.cc-sidecar",
           "caddy": "ai.hermes.caddy", "team": "ai.hermes.team"}
_TEAM_BROKEN = "couldn't install Team's components"


def _http_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=4) as r:
            return r.status < 500
    except Exception:  # noqa: BLE001
        return False


def _job_state(label: str) -> str:
    """'running', 'waiting' (loaded, not running) or '' (not installed)."""
    r = _launchctl("print", f"gui/{_uid()}/{label}")
    if r.returncode:
        return ""
    m = re.search(r"^\s*state = (\S+)", r.stdout, re.M)
    return "running" if m and m.group(1) == "running" else "waiting"


def _log_size(name: str) -> int:
    try:
        return (STATE_DIR / "logs" / name).stat().st_size
    except OSError:
        return 0


def _healthy(name: str, since: dict) -> bool | None:
    """True/False, or None when this Mac doesn't run that service (nothing to check)."""
    webui_port = os.environ.get("HERMES_WEBUI_PORT") or "8787"
    if name == "sidecar":
        return _http_ok(f"http://127.0.0.1:{os.environ.get('CC_SIDECAR_PORT') or '8794'}/health")
    if name == "webui":
        return _http_ok(f"http://127.0.0.1:{webui_port}/health")
    if sys.platform != "darwin":
        return None
    st = _job_state(_LABELS.get(name, ""))
    if not st:
        return None
    if name == "team":
        try:
            with open(STATE_DIR / "logs" / "team.log", "rb") as f:
                f.seek(since.get("team.log", 0))
                if _TEAM_BROKEN in f.read().decode("utf-8", "replace"):
                    return False
        except OSError:
            pass
    return st == "running"


def verify(needs: list[str] | None = None, *, rollback_on_fail: bool = True, timeout: float = VERIFY_S,
           since: dict | None = None) -> dict:
    """Wait for the restarted services to come back. {ok, failed: [names]}; rolls back on failure."""
    st = _state()
    needs = [n for n in (needs if needs is not None else st.get("verify", {}).get("needs", [])) if n in _LABELS]
    since = since if since is not None else st.get("verify", {}).get("since", {})
    deadline, good_since = time.time() + timeout, None
    bad: list[str] = []
    while True:
        bad = [n for n in needs if _healthy(n, since) is False]
        if not bad:
            good_since = good_since or time.time()
            if time.time() - good_since >= 15:  # still up 15 s later: not a crash loop
                break
        else:
            good_since = None
        if time.time() > deadline:
            break
        time.sleep(3)
    st = _state()
    st.pop("verify", None)
    if not bad:
        st["verified"] = {"to": st.get("to"), "at": int(time.time())}
        st.pop("failed", None) if (st.get("failed") or {}).get("to") == st.get("to") else None
        _save_state(st)
        return {"ok": True, "failed": []}
    names = {"sidecar": "the helper", "webui": "the WebUI", "gateway": "the gateway", "caddy": "HTTPS", "team": "Team"}
    step = "starting " + ", ".join(names.get(n, n) for n in bad)
    st["failed"] = {"to": st.get("to"), "step": step, "detail": "", "at": int(time.time()), "rolled_back": False}
    _save_state(st)
    if rollback_on_fail and not st.get("rollback"):
        rb = rollback(step)
        return {"ok": False, "failed": bad, "rolled_back": rb.get("ok", False), "rollback": rb}
    return {"ok": False, "failed": bad}


def rollback(reason: str = "") -> dict:
    """Return to the version before the last update and restart what that changes back."""
    st = _state()
    frm, to = st.get("from"), st.get("to")
    if not (frm and to and _SHA.match(frm) and _SHA.match(to)):
        return {"ok": False, "error": "No earlier version recorded."}
    try:
        if _git("rev-parse", "HEAD") != to:
            return {"ok": False, "error": "The code changed after that update, so it can't go back automatically."}
        files = [f for f in _git("diff", "--name-only", frm, to).splitlines() if f]
        _git("reset", "--keep", frm, timeout=60)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"Couldn't go back ({(str(exc).splitlines() or [''])[-1][:160]})"}
    changed = sorted({f[len("services/"):-len(".plist.template")] for f in files
                      if f.startswith("services/") and f.endswith(".plist.template")})
    failed = {**(st.get("failed") or {}), "to": to, "rolled_back": True, "reason": reason, "at": int(time.time())}
    _save_state({"from": to, "to": frm, "at": int(time.time()), "needs": _needs(files), "services": changed,
                 "rollback": True, "failed": failed})
    _cache.update(at=0.0, result=None)
    res = restart_detached_wait()
    return {"ok": True, "to": frm[:7], "restart": res}


def _kick_sidecar() -> None:
    if sys.platform == "win32":
        _winsvc().request_restart(["sidecar"], delay=1)
    else:
        subprocess.run(["launchctl", "kickstart", "-k", f"gui/{_uid()}/ai.hermes.cc-sidecar"], capture_output=True)


def restart_detached_wait(max_wait: float = 3600) -> dict:
    """From bin/tabbie-update (not the helper): wait until nothing is running, restart, verify."""
    end = time.time() + max_wait
    while busy() and time.time() < end:
        time.sleep(POLL)
    need = pending_restart()
    since = {"team.log": _log_size("team.log")}
    res = restart(after_reply=_kick_sidecar)
    if not res.get("ok"):
        return res
    svc = [n for n in need if n in _LABELS]
    return {**res, "verify": verify(svc, since=since, rollback_on_fail=not _state().get("rollback"))}


def schedule_verify(needs: list[str]) -> None:
    """Called by restart(): hand the health check to a separate process that outlives the helper."""
    st = _state()
    st["verify"] = {"needs": [n for n in needs if n in _LABELS], "since": {"team.log": _log_size("team.log")},
                    "at": int(time.time())}
    _save_state(st)
    if os.environ.get("TABBIE_NO_SERVICES"):
        return
    tool = ROOT / "bin" / "tabbie-update"
    if tool.exists():
        subprocess.Popen([sys.executable if sys.platform == "win32" else "/usr/bin/env", *([] if sys.platform == "win32" else ["python3"]),
                          str(tool), "verify"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True, cwd=str(ROOT))


def last_failure() -> dict | None:
    f = _state().get("failed")
    return f if isinstance(f, dict) and f.get("step") else None


# ---------------------------------------------------------------- watch (ai.hermes.updater, every 2 min)
HEAL_AFTER = 600


def watch() -> str:
    """Background check that doesn't depend on the helper running:
    - a health check whose process died mid-way is run again;
    - a restart that was waiting for Tabbie to go idle happens even if the helper is down;
    - if the helper has been down HEAL_AFTER seconds: install a newer version if there is one (it may
      be the fix), else go back from an update made in the last day."""
    st = _state()
    v = st.get("verify")
    if v and time.time() - v.get("at", 0) > VERIFY_S + 60:
        return f"verify: {verify()}"
    if sys.platform != "darwin" or not _loaded("ai.hermes.webui"):
        return "no services"
    helper = _healthy("sidecar", {})
    if helper:
        if st.get("helper_down_since"):
            st.pop("helper_down_since", None)
            _save_state(st)
        return "ok"
    if st.get("restart_when_idle") and pending_restart() and not busy():
        st.pop("restart_when_idle", None)
        _save_state(st)
        return f"restart: {restart_detached_wait()}"
    down = st.get("helper_down_since") or int(time.time())
    if not st.get("helper_down_since"):
        st["helper_down_since"] = down
        _save_state(st)
    if time.time() - down < HEAL_AFTER:
        return "helper down, waiting"
    st = _state()
    if st.get("healed_at") and time.time() - st["healed_at"] < 6 * 3600:
        return "helper down, already tried"
    st["healed_at"] = int(time.time())
    _save_state(st)
    info = check(force=True)
    if info.get("available") and info.get("remote") != (st.get("failed") or {}).get("to"):
        res = apply()
        if res.get("ok"):
            return f"healed by update: {restart_detached_wait()}"
        return f"update failed: {res.get('error')}"
    if st.get("to") and not st.get("rollback") and time.time() - st.get("at", 0) < 86400:
        return f"rolled back: {rollback('the helper stopped starting after this update')}"
    return "helper down, nothing to do"
