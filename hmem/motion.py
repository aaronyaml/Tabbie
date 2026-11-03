"""Motion studio folder: where it is, creating it from the template, and what Motion can do here.

The studio is a Remotion project the Motion panel (webui-extension/motion-studio.js) and `/motion`
jobs work in. By default it lives inside Tabbie at <tabbie>/motion/ (git-ignored) and is created
from templates/motion-studio/ the first time it's needed; nothing ever has to be there already.
A different folder (e.g. an existing studio of your own) can be named in config/motion.yaml
(`studio: ~/my-studio`, per machine, git-ignored); an existing folder is never changed.

status() is what the sidecar's /motion/status serves: booleans and hints only, never a key or token.
films() is /motion/films: the films in <studio>/studio, so any browser finds them without a file-API session.
styles() / style_media() are /motion/styles and /motion/style-media: the studio's style library (styles/) for the
Motion panel's gallery, falling back to the template's library when the studio has none.
Stdlib only (setup runs on the computer's built-in Python 3.9).
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

from .paths import hermes_home

TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "motion-studio"
MARKER = ".tabbie-studio"  # written into studios made from the template: {template version}
_DOTFILES = {"gitignore": ".gitignore", "env.example": ".env.example"}  # kept dot-less in the repo
EXTRA = [str(Path.home() / ".local" / "bin"), str(hermes_home() / "bin")] + ["/opt/homebrew/bin", "/usr/local/bin"]


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
        m = re.search(r"^studio:\s*['\"]?([^'\"#\n]+?)['\"]?\s*(?:#.*)?$", config_path().read_text(encoding="utf-8"), re.M)
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
        return (TEMPLATE / "VERSION").read_text(encoding="utf-8").strip()
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
    (tmp / MARKER).write_text(f"template {template_version()}\n", encoding="utf-8")
    if p.exists():
        p.rmdir()  # empty
    p.parent.mkdir(parents=True, exist_ok=True)
    os.replace(tmp, p)
    return {"path": str(p), "created": True}


# ── template additions for studios made from an older template ──────────────────────────────────
# A studio is the user's once created, so updates never overwrite it. Things a newer template adds that a
# film can't break (the sound-effects library and its code) are copied in once: only missing files, and
# manifest entries the studio doesn't have yet. Studios not made from the template are never touched.
ADDITIONS = ("public/audio/sfx", "src/audio", "styles")


def _ver(text: str) -> int:
    m = re.search(r"(\d+)", text or "")
    return int(m.group(1)) if m else 0


def sync_template(p: Path | None = None) -> list[str]:
    """Bring a template-made studio up to the current template's additions. Returns the files added."""
    p = p or studio_dir()
    marker = p / MARKER
    try:
        have = _ver(marker.read_text(encoding="utf-8"))
    except OSError:
        return []  # not made from the template: leave it alone
    want = _ver(template_version())
    if not want or have >= want:
        return []
    added = []
    for rel in ADDITIONS:
        src = TEMPLATE / rel
        if not src.is_dir():
            continue
        for f in sorted(src.rglob("*")):
            if not f.is_file() or f.name == ".DS_Store":
                continue
            dst = p / f.relative_to(TEMPLATE)
            if dst.name == "manifest.json" and dst.exists():
                added += _merge_manifest(f, dst)
                continue
            if not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dst)
                added.append(f.relative_to(TEMPLATE).as_posix())
    # sections a newer template added to its docs: appended once, the rest of the user's file untouched
    for name, head in (("CLAUDE.md", "## Sound effects"), ("MOTION_RULES.md", "## Transitions"), ("MOTION_RULES.md", "## Style")):
        doc, tdoc = p / name, TEMPLATE / name
        try:
            text, ttext = doc.read_text(encoding="utf-8"), tdoc.read_text(encoding="utf-8")
            m = re.search(rf"^{re.escape(head)}.*?(?=^## |\Z)", ttext, re.M | re.S)
            if m and head not in text:
                doc.write_text(text.rstrip("\n") + "\n\n" + m.group(0).rstrip("\n") + "\n", encoding="utf-8")
                added.append(f"{name} ({m.group(0).splitlines()[0][3:].split(':')[0]})")
        except OSError:
            pass
    marker.write_text(f"template {want}\n", encoding="utf-8")
    return added


def _merge_manifest(src: Path, dst: Path) -> list[str]:
    try:
        new, cur = json.loads(src.read_text(encoding="utf-8")), json.loads(dst.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    sounds = cur.setdefault("sounds", {})
    if not isinstance(sounds, dict):
        return []
    extra = {k: v for k, v in (new.get("sounds") or {}).items() if k not in sounds}
    if not extra:
        return []
    sounds.update(extra)
    dst.write_text(json.dumps(cur, indent=1), encoding="utf-8")
    return [f"manifest.json (+{len(extra)} sounds)"]


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
            with open(log, "w", encoding="utf-8") as f:
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
        d = json.loads(_inst_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    st = d.get("state", "")
    if st == "running" and not _alive(d.get("pid")):
        st, d["error"] = "failed", "The install stopped part-way (Tabbie restarted). Try again."
    return {"state": st, "step": d.get("step", ""), "error": d.get("error", "")}


def _alive(pid) -> bool:
    from . import procutil
    try:
        return procutil.alive(int(pid))
    except (TypeError, ValueError):
        return False


def _set(**kw) -> None:
    f = _inst_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    d.update(kw, pid=os.getpid(), at=int(time.time()))
    tmp = f.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(d), encoding="utf-8")
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
                with open(logs / "motion-node.log", "w", encoding="utf-8") as f:
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
    """{films: [{project, title, step, updatedAt, sessionId?, for?: {id, name}}]}, newest first, read from each film's
    project.json (written by the Motion panel). The folder name is the project; unreadable ones are skipped."""
    d = (p or studio_dir()) / "studio"
    out = []
    try:
        dirs = sorted(x for x in d.iterdir() if x.is_dir() and not x.name.startswith("."))
    except OSError:
        dirs = []
    for x in dirs:
        try:
            pj = json.loads((x / "project.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(pj, dict):
            continue
        f = {"project": x.name, "title": str(pj.get("title") or x.name), "step": str(pj.get("step") or "audio"),
             "updatedAt": str(pj.get("updatedAt") or pj.get("createdAt") or "")}
        if isinstance(pj.get("sessionId"), str) and pj["sessionId"]:
            f["sessionId"] = pj["sessionId"]
        c = pj.get("context")
        if isinstance(c, dict) and isinstance(c.get("project"), str) and c["project"]:
            f["for"] = {"id": c["project"], "name": str(c.get("name") or c["project"])}  # the Tabbie project it's made for
        out.append(f)
    out.sort(key=lambda f: f["updatedAt"], reverse=True)
    return {"films": out}


# ── the style library (gallery) ────────────────────────────────────────────────────────────────
_STYLE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
_MEDIA = {"thumb": ("thumb.jpg", "image/jpeg"), "preview": ("preview.mp4", "video/mp4"),
          "html": ("thumb.html", "text/plain; charset=utf-8")}  # the live thumbnail (HTML/CSS on the palette variables), as text: the panel puts it in a sandboxed frame, never served as a page
MEDIA_MAX = 500 * 1024  # the WebUI proxy's response cap is 512 KB; styles:build keeps previews under it


def _styles_root(p: Path | None = None) -> Path:
    s = (p or studio_dir()) / "styles"
    return s if (s / "index.json").is_file() else TEMPLATE / "styles"


def styles(p: Path | None = None) -> dict:
    """{styles: [...]} in the library's order: what the gallery card and preview need from each style.json
    (name, tagline, description, swatches, type, motion summary, transitions) + which media exist."""
    d = _styles_root(p)
    try:
        order = json.loads((d / "index.json").read_text(encoding="utf-8")).get("order") or []
    except (OSError, ValueError, AttributeError):
        order = []
    out = []
    for sid in order:
        if not isinstance(sid, str) or not _STYLE_ID.match(sid):
            continue
        try:
            st = json.loads((d / sid / "style.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(st, dict):
            continue
        ty, mo, pal = st.get("type") or {}, st.get("motion") or {}, st.get("palette") or {}
        fam = lambda k: (ty.get(k) or {}).get("family", "") if isinstance(ty.get(k), dict) else ""
        out.append({
            "id": sid, "number": str(st.get("number") or ""), "name": str(st.get("name") or sid),
            "tagline": str(st.get("tagline") or ""), "description": str(st.get("description") or ""),
            "family": str(st.get("family") or "core"), "mood": list(st.get("mood") or [])[:6],
            "bestFor": list(st.get("bestFor") or [])[:6], "avoidFor": list(st.get("avoidFor") or [])[:4],
            "swatches": [c for c in (pal.get("swatches") or []) if isinstance(c, str)][:6],
            "bg": str(pal.get("bg") or ""), "ink": str(pal.get("ink") or ""), "accent": str(pal.get("accent") or ""),
            # the colour roles a film can recolour (project.json style.overrides.palette); solid hex only
            "palette": {k: v for k, v in list(pal.items())[:12] if k != "swatches" and isinstance(v, str) and _HEX.match(v)},
            "display": fam("display"), "text": fam("text"),
            "step": int(mo.get("step") or 1) if str(mo.get("step") or "1").isdigit() else 1,
            "signature": list((st.get("transitions") or {}).get("signature") or [])[:4],
            "camera": str(st.get("camera") or ""), "sound": str(st.get("sound") or ""),
            "fonts": [{"family": f["family"], "weights": [str(w) for w in (f.get("weights") or [])][:4]}
                      for f in (ty.get(k) for k in ("display", "text", "mono")) if isinstance(f, dict) and f.get("family")],
            **_edit_system(st),
            "thumb": (d / sid / "thumb.jpg").is_file(), "preview": (d / sid / "preview.mp4").is_file(),
            "html": (d / sid / "thumb.html").is_file(),
        })
    return {"styles": out}


def _txt(v, n=400) -> str:
    return str(v)[:n] if isinstance(v, (str, int, float)) else ""


def _edit_system(st: dict) -> dict:
    """motion.style/2: how the style moves, cuts and sequences (what the gallery's detail view explains)."""
    g, seq, ex = st.get("grammar") or {}, st.get("sequence") or {}, st.get("exampleSequence") or {}
    lst = lambda v: v if isinstance(v, list) else []
    arch = {a.get("id"): a for a in lst(st.get("archetypes")) if isinstance(a, dict)}
    pre = {p.get("id"): p for p in lst(st.get("transitionPresets")) if isinstance(p, dict)}
    name = lambda m, k: _txt((m.get(k) or {}).get("name") or k or "", 80)
    live = st.get("live") if isinstance(st.get("live"), dict) else {}
    return {
        "motionSummary": _txt(st.get("motionSummary")),
        "grammar": {k: _txt(g.get(k)) for k in ("pacing", "rhythm", "easing", "acceleration", "density", "holds")} | {"movement": [_txt(x, 160) for x in lst(g.get("movement"))][:8]},
        "presets": [{"id": _txt(p.get("id"), 60), "name": _txt(p.get("name"), 80), "kind": _txt(p.get("kind"), 40), "bridge": _txt(p.get("bridge"), 200), "build": _txt(p.get("build"))} for p in pre.values()][:8],
        "archetypes": [{"id": _txt(a.get("id"), 60), "name": _txt(a.get("name"), 80), "purpose": _txt(a.get("purpose"), 200), "composition": _txt(a.get("composition"), 300), "motion": _txt(a.get("motion"), 300)} for a in arch.values()][:6],
        "sequence": [{"id": _txt(b.get("id"), 40), "share": float(b.get("share") or 0) if isinstance(b.get("share"), (int, float)) else 0, "purpose": _txt(b.get("purpose"), 200),
                      "archetypes": [name(arch, x) for x in lst(b.get("archetypes"))][:3], "out": name(pre, b.get("transitionOut")) if b.get("transitionOut") else ""}
                     for b in lst(seq.get("beats")) if isinstance(b, dict)][:7],
        "cutRhythm": _txt(seq.get("cutRhythm")),
        "example": {"title": _txt(ex.get("title"), 120), "scenes": [{"archetype": name(arch, sc.get("archetype")), "beat": _txt(sc.get("beat"), 40), "action": _txt(sc.get("action"), 300),
                    "out": name(pre, sc.get("transitionOut")) if sc.get("transitionOut") else ""} for sc in lst(ex.get("scenes")) if isinstance(sc, dict)][:7]},
        "elements": {k: _txt(v, 300) for k, v in (st.get("elements") or {}).items() if isinstance(v, str)} if isinstance(st.get("elements"), dict) else {},
        "audio": {"drive": [_txt(x, 40) for x in lst((st.get("audio") or {}).get("drive"))][:5], "sync": _txt((st.get("audio") or {}).get("sync"))} if isinstance(st.get("audio"), dict) else {},
        "live": {"seconds": float(live.get("seconds") or 6), "rest": float(live.get("rest") or 0),
                 "beats": [{"at": float(b.get("at") or 0), "label": _txt(b.get("label"), 60)} for b in lst(live.get("beats")) if isinstance(b, dict)][:6]},
    }


def style_media(sid: str, kind: str, p: Path | None = None) -> tuple[bytes, str] | None:
    """A style's thumb.jpg or preview.mp4 as (bytes, content type); None when missing, unknown or too big."""
    if not _STYLE_ID.match(sid or "") or kind not in _MEDIA:
        return None
    name, ctype = _MEDIA[kind]
    f = _styles_root(p) / sid / name
    try:
        if not f.is_file() or f.stat().st_size > MEDIA_MAX:
            return None
        return f.read_bytes(), ctype
    except OSError:
        return None


# ── what's linked (for the Generate buttons) ──────────────────────────────────────────────────
def _tilde(p: Path) -> str:
    """Show a path under home as ~/…: shorter, and never puts the user's name on screen."""
    try:
        return "~/" + str(Path(p).relative_to(Path.home()))
    except ValueError:
        return str(p)


def _env_key(p: Path, name: str) -> bool:
    if os.environ.get(name, "").strip():
        return True
    try:
        text = (p / ".env").read_text(encoding="utf-8")
    except OSError:
        return False
    return re.search(rf"^\s*(?:export\s+)?{name}\s*=\s*['\"]?[^\s'\"#]+", text, re.M) is not None


_KEY = re.compile(r"^[A-Za-z0-9_-]{20,200}$")


def _check_elevenlabs(key: str) -> str:
    """"ok", "bad" (ElevenLabs says the key is invalid) or "unknown" (couldn't ask). A key limited to
    music may not read /v1/user: only an explicit invalid-key answer counts as bad."""
    import urllib.error
    import urllib.request
    req = urllib.request.Request("https://api.elevenlabs.io/v1/user", headers={"xi-api-key": key})
    try:
        with urllib.request.urlopen(req, timeout=8):
            return "ok"
    except urllib.error.HTTPError as exc:
        body = exc.read(2000).decode("utf-8", "replace").lower()
        return "bad" if exc.code == 401 and "invalid" in body else "ok"
    except (OSError, ValueError):
        return "unknown"


def set_elevenlabs_key(key: str, check=_check_elevenlabs) -> tuple[int, dict]:
    """Save the user's ElevenLabs API key in the studio's .env (git-ignored, 0600). Never logged or
    echoed back. Replaces an existing ELEVENLABS_API_KEY line, keeps every other line."""
    key = (key or "").strip()
    if not _KEY.match(key):
        return 400, {"error": "That doesn't look like an ElevenLabs API key. Copy it again from elevenlabs.io."}
    p = studio_dir()
    if not p.is_dir():
        return 409, {"error": "Install Motion first, then connect ElevenLabs."}
    verdict = check(key)
    if verdict == "bad":
        return 400, {"error": "ElevenLabs says that key isn't valid. Check it and try again."}
    env = p / ".env"
    try:
        lines = env.read_text(encoding="utf-8").splitlines() if env.exists() else []
    except OSError:
        lines = []
    lines = [ln for ln in lines if not re.match(r"^\s*(?:export\s+)?ELEVENLABS_API_KEY\s*=", ln)]
    lines.append(f"ELEVENLABS_API_KEY={key}")
    tmp = env.with_name(".env.tabbie-tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, env)
    os.chmod(env, 0o600)
    return 200, {"ok": True, "checked": verdict == "ok"}


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
    if exists:
        try:
            out["synced"] = sync_template(p)
        except OSError as exc:
            out["sync_error"] = str(exc)[:200]
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
            "mcp": (hermes_home() / "mcp-tokens" / "elevenlabs.json").is_file(),
            "hint": "Generate audio needs ElevenLabs: connect it with your API key (elevenlabs.io → Developers → "
                    f"API keys), or add ELEVENLABS_API_KEY=<your key> to {_tilde(p)}/.env yourself.",
        },
        "higgsfield": {
            "linked": higgsfield_linked(),
            "hint": "To generate assets, link Higgsfield: run `higgsfield auth login` in Terminal, then reopen Motion.",
        },
    })
    return out
