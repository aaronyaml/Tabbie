"""Motion studio folder: where it is, creating it from the template, and what Motion can do here.

The studio is a Remotion project the Motion panel (webui-extension/motion-studio.js) and `/motion`
jobs work in. By default it lives inside Tabbie at <tabbie>/motion/ (git-ignored) and is created
from templates/motion-studio/ the first time it's needed; nothing ever has to be there already.
A different folder (e.g. an existing studio of your own) can be named in config/motion.yaml
(`studio: ~/my-studio`, per machine, git-ignored); an existing folder is never changed.

status() is what the sidecar's /motion/status serves: booleans and hints only, never a key or token.
films() is /motion/films: the films in <studio>/studio, so any browser finds them without a file-API session.
Stdlib only (setup runs on the Mac's built-in Python 3.9).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "motion-studio"
MARKER = ".tabbie-studio"  # written into studios made from the template: {template version}
_DOTFILES = {"gitignore": ".gitignore", "env.example": ".env.example"}  # kept dot-less in the repo
EXTRA = [str(Path.home() / p) for p in (".local/bin", ".hermes/bin")] + ["/opt/homebrew/bin", "/usr/local/bin"]


def root() -> Path:
    return Path(os.environ.get("COMMAND_CENTRE_HOME", Path.home() / "hermes")).expanduser()


def config_path() -> Path:
    return root() / "config" / "motion.yaml"


def studio_dir() -> Path:
    """TABBIE_MOTION_STUDIO, else `studio:` in config/motion.yaml, else <tabbie>/motion."""
    env = os.environ.get("TABBIE_MOTION_STUDIO", "").strip()
    if env:
        return Path(env).expanduser()
    try:
        m = re.search(r"^studio:\s*['\"]?([^'\"#\n]+?)['\"]?\s*(?:#.*)?$", config_path().read_text(), re.M)
    except OSError:
        m = None
    if m and m.group(1).strip() not in ("", "null", "~"):
        p = Path(m.group(1).strip()).expanduser()
        return p if p.is_absolute() else root() / p
    return root() / "motion"


def _which(cmd: str) -> str | None:
    return shutil.which(cmd, path=os.pathsep.join([os.environ.get("PATH", ""), *EXTRA]))


def template_version() -> str:
    try:
        return (TEMPLATE / "VERSION").read_text().strip()
    except OSError:
        return ""


def ensure(path: Path | None = None) -> dict:
    """Create the studio from the template when the folder is missing (or empty). Never touches a
    folder that already has files in it. Returns {path, created}."""
    p = path or studio_dir()
    if p.exists() and (not p.is_dir() or any(p.iterdir())):
        return {"path": str(p), "created": False}
    if not TEMPLATE.is_dir():
        raise RuntimeError("The Motion template is missing from Tabbie (templates/motion-studio).")
    tmp = p.with_name(f".{p.name}.creating-{os.getpid()}")
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.copytree(TEMPLATE, tmp, ignore=shutil.ignore_patterns("node_modules", "VERSION", ".DS_Store"))
    for src, dst in _DOTFILES.items():
        if (tmp / src).exists():
            os.replace(tmp / src, tmp / dst)
    (tmp / MARKER).write_text(f"template {template_version()}\n")
    if p.exists():
        p.rmdir()  # empty
    p.parent.mkdir(parents=True, exist_ok=True)
    os.replace(tmp, p)
    return {"path": str(p), "created": True}


# ── npm install (once per studio, in the background) ───────────────────────────────────────────
_npm: dict = {}
_npm_lock = threading.Lock()


def deps_ready(p: Path) -> bool:
    return (p / "node_modules" / ".bin" / "remotion").exists() or (p / "node_modules" / "remotion").is_dir()


def npm_install(p: Path | None = None, wait: bool = False) -> dict:
    """Start `npm install` in the studio unless it's done or running. {state: ready|installing|failed|no-node}."""
    p = p or studio_dir()
    if deps_ready(p):
        return {"state": "ready"}
    npm = _which("npm")
    if not npm:
        return {"state": "no-node"}
    key = str(p)
    with _npm_lock:
        job = _npm.get(key)
        if job and job["proc"].poll() is None:
            pass
        elif job and job["proc"].returncode != 0 and time.time() - job["at"] < 60:
            return {"state": "failed", "log": job["log"]}
        else:
            logs = root() / "state" / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            log = logs / "motion-npm.log"
            env = {**os.environ, "PATH": os.pathsep.join([str(Path(npm).parent), os.environ.get("PATH", ""), *EXTRA])}
            with open(log, "w") as f:
                proc = subprocess.Popen([npm, "install", "--no-audit", "--no-fund", "--loglevel=error"], cwd=str(p),
                                        stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env)
            job = _npm[key] = {"proc": proc, "at": time.time(), "log": str(log)}
    if wait:
        job["proc"].wait()
        return {"state": "ready" if deps_ready(p) else "failed", "log": job["log"]}
    return {"state": "installing"}


# ── one-click install (Motion panel's Download button, or /motion when it isn't installed) ────
# The panel (sidecar) and /motion (the WebUI's hub) run in different processes, so the progress lives
# in state/motion-install.json with the installer's pid: whichever starts first runs it, both see it.
_inst_lock = threading.Lock()


def installed(p: Path | None = None) -> bool:
    p = p or studio_dir()
    return bool(_which("node")) and p.is_dir() and deps_ready(p)


def _inst_file() -> Path:
    return root() / "state" / "motion-install.json"


def install_state() -> dict:
    try:
        d = json.loads(_inst_file().read_text())
    except (OSError, ValueError):
        d = {}
    st = d.get("state", "")
    if st == "running" and not _alive(d.get("pid")):
        st, d["error"] = "failed", "The install stopped part-way (Tabbie restarted). Try again."
    return {"state": st, "step": d.get("step", ""), "error": d.get("error", "")}


def _alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (TypeError, ValueError, OSError):
        return False


def _set(**kw) -> None:
    f = _inst_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    try:
        d = json.loads(f.read_text())
    except (OSError, ValueError):
        d = {}
    d.update(kw, pid=os.getpid(), at=int(time.time()))
    tmp = f.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(d))
    os.replace(tmp, f)


def install() -> dict:
    """Start installing whatever Motion is missing, in the background. {state: running|done|failed, step}."""
    with _inst_lock:
        if install_state()["state"] == "running":
            return install_state()
        if installed():
            _set(state="done", step="", error="")
            return install_state()
        _set(state="running", step="", error="")

    def run():
        try:
            if not _which("node"):
                _set(step="node")
                logs = root() / "state" / "logs"
                logs.mkdir(parents=True, exist_ok=True)
                with open(logs / "motion-node.log", "w") as f:
                    rc = subprocess.run([str(root() / "bin" / "install-tool"), "node"], stdout=f, stderr=subprocess.STDOUT,
                                        stdin=subprocess.DEVNULL, timeout=900).returncode
                if rc or not _which("node"):
                    raise RuntimeError("Node.js didn't install (state/logs/motion-node.log). Check the internet connection and try again.")
            _set(step="studio")
            p = ensure()["path"]
            _set(step="packages")
            if npm_install(Path(p), wait=True)["state"] != "ready":
                raise RuntimeError("The studio's packages didn't install (state/logs/motion-npm.log). Try again.")
            _set(state="done", step="")
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            _set(state="failed", error=str(exc)[:200])

    threading.Thread(target=run, daemon=True, name="motion-install").start()
    return install_state()


# ── the films in <studio>/studio (the panel's home list) ──────────────────────────────────────
def films(p: Path | None = None) -> dict:
    """{films: [{project, title, step, updatedAt, sessionId?}]}, newest first, read from each film's
    project.json (written by the Motion panel). The folder name is the project; unreadable ones are skipped."""
    d = (p or studio_dir()) / "studio"
    out = []
    try:
        dirs = sorted(x for x in d.iterdir() if x.is_dir() and not x.name.startswith("."))
    except OSError:
        dirs = []
    for x in dirs:
        try:
            pj = json.loads((x / "project.json").read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(pj, dict):
            continue
        f = {"project": x.name, "title": str(pj.get("title") or x.name), "step": str(pj.get("step") or "audio"),
             "updatedAt": str(pj.get("updatedAt") or pj.get("createdAt") or "")}
        if isinstance(pj.get("sessionId"), str) and pj["sessionId"]:
            f["sessionId"] = pj["sessionId"]
        out.append(f)
    out.sort(key=lambda f: f["updatedAt"], reverse=True)
    return {"films": out}


# ── what's linked (for the Generate buttons) ──────────────────────────────────────────────────
def _env_key(p: Path, name: str) -> bool:
    if os.environ.get(name, "").strip():
        return True
    try:
        text = (p / ".env").read_text()
    except OSError:
        return False
    return re.search(rf"^\s*(?:export\s+)?{name}\s*=\s*['\"]?[^\s'\"#]+", text, re.M) is not None


_hf: dict = {"at": 0.0, "ok": False}


def higgsfield_linked() -> bool:
    """Same check as `bin/hub health`: the Higgsfield CLI is signed in. Cached (it's a network call)."""
    if time.time() - _hf["at"] < (300 if _hf["ok"] else 30):
        return _hf["ok"]
    exe = _which("higgsfield")
    ok = False
    if exe:
        try:
            ok = subprocess.run([exe, "account", "status"], capture_output=True, timeout=6,
                                stdin=subprocess.DEVNULL).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            ok = False
    _hf.update(at=time.time(), ok=ok)
    return ok


def status(create: bool = False) -> dict:
    """What the Motion panel needs to know. Booleans and hints only: never a key, token or log line."""
    p = studio_dir()
    out: dict = {"path": str(p), "created": False}
    if create:
        try:
            out["created"] = ensure(p)["created"]
        except (OSError, RuntimeError) as exc:
            out["error"] = str(exc)[:200]
    exists = p.is_dir()
    node = _which("node")
    npm = npm_install(p) if (create and exists) else {"state": "ready" if exists and deps_ready(p) else
                                                       ("no-node" if not node else "missing")}
    out.update({
        "exists": exists, "node": bool(node), "deps": npm["state"],
        "installed": bool(node) and exists and deps_ready(p), "install": install_state(),
        "template": (p / MARKER).exists(),
        "elevenlabs": {
            # Music generation calls ElevenLabs' Music API, which needs the account's API key in the
            # studio's .env. The hosted ElevenLabs MCP (`hermes mcp login elevenlabs`) has no music tool.
            "linked": exists and _env_key(p, "ELEVENLABS_API_KEY"),
            "mcp": (Path.home() / ".hermes" / "mcp-tokens" / "elevenlabs.json").is_file(),
            "hint": f"To generate music, link ElevenLabs: add ELEVENLABS_API_KEY=<your key> to {p}/.env "
                    "(elevenlabs.io → Developers → API keys), then reopen Motion.",
        },
        "higgsfield": {
            "linked": higgsfield_linked(),
            "hint": "To generate assets, link Higgsfield: run `higgsfield auth login` in Terminal, then reopen Motion.",
        },
    })
    return out
