"""First-class Projects: one logical project = several linked folders/repos.

Registry: `memory/projects/<id>/project.json`, inside the memory git repo, so every change is
versioned, attributable and reversible. Writes take the store lock, check `expected_hash` (optimistic
concurrency: a stale editor gets ConflictError instead of overwriting), write atomically, commit.

Identity is the id (`<slug>-<4 hex>`), fixed at creation; renaming changes only `name`. Memory,
traces, proposals and dream runs all key on the id.

Folder policy (the filesystem belongs to the Mac running Hermes; the browser may be an iPhone):
- realpath'd existing directory under the allowed root ($HOME), never $HOME itself, never a hidden
  top-level folder (~/.ssh, ~/.hermes, …), ~/Library or Hermes' own state;
- at most one active project per folder, and no nesting (inside/containing an attached folder).
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
from pathlib import Path

from . import doc as D
from . import store

SCHEMA = "hermes.project/1"
MAX_INSTRUCTIONS = 16_000
MAX_DESCRIPTION = 600
MAX_FOLDERS = 24
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}-[0-9a-f]{4}$")
_FID = re.compile(r"^f-[0-9a-f]{6}$")
CONTEXT_FILES = ("CLAUDE.md", "AGENTS.md", ".hermes/context.md", ".hermes/architecture.md")
CONTEXT_DIRS = (".hermes/decisions", ".hermes/workflows")
DREAMER_DEFAULTS = {"after_sessions": 10}


class ProjectError(store.MemoryError_):
    pass


# ------------------------------------------------------------------ paths

def allowed_root() -> Path:
    return Path(os.environ.get("HERMES_PROJECT_ROOT") or Path.home()).resolve()


def workspaces_dir() -> Path:
    """Symlink workspace per project (what the WebUI session's workspace points at)."""
    return Path(os.environ.get("HERMES_WORKSPACES_DIR") or store.root().parent / "workspaces")


def _pdir(pid: str) -> Path:
    if not _ID.match(pid or ""):
        raise ProjectError(f"bad project id {pid!r}")
    return store.root() / "projects" / pid


def _pfile(pid: str) -> Path:
    return _pdir(pid) / "project.json"


def tilde(p: str) -> str:
    h = str(Path.home())
    return "~" + p[len(h):] if p == h or p.startswith(h + "/") else p


_PERSONAL = {"Library", "Applications", "Public", "Desktop", "Documents", "Downloads", "Movies", "Music", "Pictures",
             "Pictures Library", "Mobile Documents"}


def _forbidden(p: Path) -> str | None:
    """Why this real path may not be attached (None = fine)."""
    base = allowed_root()
    if p == base:
        return "that is the whole home folder; pick the project folder inside it"
    if not p.is_relative_to(base):
        return f"only folders inside {tilde(str(base))} can be attached"
    rel = p.relative_to(base).parts
    if rel[0].startswith("."):
        return "hidden folders (like ~/.ssh or ~/.hermes) can't be attached"
    if rel[0] in _PERSONAL:
        return f"~/{rel[0]} holds personal files; attach the project folder itself from elsewhere"
    hermes = store.root().parent.resolve()
    if p == hermes:
        return "the Hermes folder itself can't be attached (it holds memory and state)"
    for own in ("memory", "state", "backups", "workspaces"):
        if p == hermes / own or p.is_relative_to(hermes / own):
            return "Hermes' own memory/state folders can't be attached"
    if hermes.is_relative_to(p):
        return "that folder contains Hermes' own memory/state"
    if any(part.startswith(".") and part not in (".hermes",) for part in rel):
        return "folders inside hidden folders can't be attached"
    return None


def true_case(p: Path) -> Path:
    """Spell an existing real path the way the disk does. APFS is case-insensitive, and resolve() keeps
    the caller's spelling, so ~/LIBRARY would otherwise slip past every name check below."""
    out = Path(p.anchor)
    for part in p.parts[1:]:
        try:
            names = os.listdir(out)
        except OSError:
            return Path(out, *p.parts[len(out.parts):])
        if part not in names:
            part = next((n for n in names if n.casefold() == part.casefold()), part)
        out = out / part
    return out


def canonical(path: str) -> Path:
    raw = str(path or "").strip()
    if not raw or "\x00" in raw:
        raise ProjectError("empty path")
    p = Path(os.path.expanduser(raw))
    if not p.is_absolute():
        raise ProjectError("use a full path (e.g. ~/acme-app)")
    try:
        real = true_case(p.resolve(strict=True))
    except (OSError, RuntimeError):
        raise ProjectError(f"{tilde(str(p))} doesn't exist on this Mac") from None
    if not real.is_dir():
        raise ProjectError(f"{tilde(str(real))} is not a folder")
    why = _forbidden(real)
    if why:
        raise ProjectError(why)
    return real


# a linked repo's .git/config is not trusted: no fsmonitor/hooks/pager, no index lock taken by a status
_GIT_SAFE = ("--no-optional-locks", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
             "-c", "core.pager=cat", "-c", "core.untrackedCache=false")


def _git_meta(p: Path) -> dict | None:
    def g(*a):
        try:
            r = subprocess.run(["git", *_GIT_SAFE, "-C", str(p), *a], capture_output=True, text=True, timeout=5,
                               env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
            return r.stdout.strip() if r.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            return ""
    top = g("rev-parse", "--show-toplevel")
    if not top:
        return None
    remote = g("config", "--get", "remote.origin.url")
    remote = re.sub(r"^[a-z+]+://", "", remote)
    remote = re.sub(r"^[^@/]+@", "", remote).replace(":", "/", 1) if remote else ""  # drop user:token@
    return {"root": top, "remote": remote.removesuffix(".git")[:200]}


def git_live(path: str) -> dict:
    """Branch / head / dirty count, read live (never stored: it changes every commit)."""
    def g(*a):
        try:
            r = subprocess.run(["git", *_GIT_SAFE, "-C", path, *a], capture_output=True, text=True, timeout=5,
                               env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
            return r.stdout.strip() if r.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            return ""
    if not folder_ok(path):
        return {"branch": "", "head": "", "dirty": 0, "missing": True}
    st = g("status", "--porcelain")
    return {"branch": g("rev-parse", "--abbrev-ref", "HEAD"), "head": g("rev-parse", "--short", "HEAD"),
            "dirty": len([x for x in st.splitlines() if x.strip()])}


_SAFE_NAME = re.compile(r"^[\w][\w .@+()&,'-]{0,120}$")


def safe_name(n: str) -> bool:
    """Folder labels and context-file names reach worker prompts: plain one-line names only."""
    return bool(_SAFE_NAME.match(n or "")) and n == n.strip()


def context_files(p: Path) -> list[str]:
    out = [f for f in CONTEXT_FILES if (p / f).is_file()]
    for d in CONTEXT_DIRS:
        if (p / d).is_dir():
            out += sorted(f"{d}/{x.name}" for x in (p / d).glob("*.md") if safe_name(x.name))[:20]
    return out


def folder_ok(path: str) -> bool:
    """Re-check at use time: a folder whose realpath moved (symlink swap) or vanished is skipped."""
    try:
        return str(canonical(path)) == path
    except ProjectError:
        return False


def folder_record(path: str) -> dict:
    real = canonical(path)
    if not safe_name(real.name):
        raise ProjectError("rename that folder first: its name has characters Hermes won't put into prompts")
    return {"id": "f-" + secrets.token_hex(3), "path": str(real), "label": real.name, "added_at": D.now_iso(),
            "git": _git_meta(real), "context_files": context_files(real)}


# ------------------------------------------------------------------ read

_CACHE: dict = {"sig": None, "all": []}


def _sig() -> tuple:
    base = store.root() / "projects"
    out = []
    if base.is_dir():
        for f in base.glob("*/project.json"):
            try:
                out.append((f.parent.name, f.stat().st_mtime_ns))
            except OSError:
                pass
    return tuple(sorted(out))


def load(pid: str) -> tuple[dict, str]:
    f = _pfile(pid)
    try:
        text = f.read_text()
    except OSError:
        raise ProjectError(f"no project {pid}") from None
    return json.loads(text), D.sha(text)


def all_projects(include_archived: bool = False) -> list[dict]:
    sig = _sig()
    if sig != _CACHE["sig"]:
        rows = []
        for pid, _ in sig:
            try:
                rows.append(load(pid)[0])
            except (ProjectError, ValueError):
                continue
        _CACHE.update(sig=sig, all=rows)
    rows = [dict(p) for p in _CACHE["all"]]
    return [p for p in rows if include_archived or p.get("status") == "active"]


def get(pid: str) -> dict | None:
    try:
        return load(pid)[0]
    except (ProjectError, ValueError):
        return None


def find(name_or_id: str) -> dict | None:
    """By id, slug or (case-insensitive) name; active projects only."""
    q = str(name_or_id or "").strip().lower()
    for p in all_projects():
        if q in (p["id"], p.get("slug"), str(p.get("name", "")).lower()):
            return p
    return None


def project_for_path(path: str | None) -> str | None:
    """Id of the active project that owns this path (inside a linked folder or its workspace dir)."""
    if not path:
        return None
    try:
        rp = os.path.realpath(os.path.expanduser(path))
    except (OSError, ValueError):
        return None
    ws = os.path.realpath(str(workspaces_dir()))
    if rp == ws or rp.startswith(ws + os.sep):
        name = rp[len(ws) + 1:].split(os.sep)[0]
        return name if name and get(name) and get(name).get("status") == "active" else None
    best, best_len = None, -1
    for p in all_projects():
        for f in p.get("folders", []):
            fp = f["path"]
            if (rp == fp or rp.startswith(fp + os.sep)) and len(fp) > best_len:
                best, best_len = p["id"], len(fp)
    return best


def primary(p: dict) -> dict | None:
    return next((f for f in p.get("folders", []) if f["id"] == p.get("primary_folder")), None) or \
        (p.get("folders") or [None])[0]


# ------------------------------------------------------------------ write

def _check_folder_free(p: dict, real: str, others: list[dict]) -> None:
    for f in p.get("folders", []):
        fp = f["path"]
        if fp == real:
            raise ProjectError(f"{tilde(real)} is already in this project")
        if real.startswith(fp + os.sep):
            raise ProjectError(f"{tilde(real)} is inside {tilde(fp)}, which is already in this project")
        if fp.startswith(real + os.sep):
            raise ProjectError(f"{tilde(real)} contains {tilde(fp)}, which is already in this project; remove that first")
    for o in others:
        for f in o.get("folders", []):
            fp = f["path"]
            if fp == real or real.startswith(fp + os.sep) or fp.startswith(real + os.sep):
                raise ProjectError(f"{tilde(real)} overlaps {tilde(fp)}, which belongs to project “{o['name']}”")


def _write(p: dict, actor: str, message: str, created: bool = False) -> str:
    p["updated_at"] = D.now_iso()
    p["version"] = int(p.get("version") or 0) + 1
    text = json.dumps(p, ensure_ascii=False, indent=1) + "\n"
    store._atomic_write(_pfile(p["id"]), text)
    ins = instructions_path(p["id"])
    if p.get("instructions"):
        store._atomic_write(ins, f"# {p['name']} — project instructions (written by the user)\n\n{p['instructions']}\n")
    elif ins.exists():
        ins.unlink()
    store.commit([f"projects/{p['id']}"], actor, message)
    store.append_event("project_created" if created else "project_updated", id=p["id"], actor=actor,
                       version=p["version"])
    return D.sha(text)


def _validate(p: dict) -> None:
    if not str(p.get("name") or "").strip():
        raise ProjectError("a project needs a name")
    if len(p["name"]) > 80:
        raise ProjectError("name too long (80 characters max)")
    if len(p.get("description") or "") > MAX_DESCRIPTION:
        raise ProjectError(f"description too long ({MAX_DESCRIPTION} characters max)")
    if len(p.get("instructions") or "") > MAX_INSTRUCTIONS:
        raise ProjectError(f"instructions too long ({MAX_INSTRUCTIONS // 1000} KB max)")
    if len(p.get("folders") or []) > MAX_FOLDERS:
        raise ProjectError(f"at most {MAX_FOLDERS} folders per project")
    if p.get("status") not in ("active", "archived"):
        raise ProjectError("bad status")
    ids = [f["id"] for f in p.get("folders", [])]
    if p.get("primary_folder") and p["primary_folder"] not in ids:
        raise ProjectError("primary folder must be one of the project's folders")


def create(name: str, *, description: str = "", folders: list[str] | None = None, primary_path: str | None = None,
           instructions: str = "", actor: str = "user") -> dict:
    store.init()
    name = re.sub(r"\s+", " ", str(name or "")).strip()
    base = D.slug(name)[:32].strip("-") or "project"
    with store._lock("store"):
        others = all_projects()
        if any(str(o.get("name", "")).lower() == name.lower() for o in others):
            raise ProjectError(f"a project called “{name}” already exists")
        pid = f"{base}-{secrets.token_hex(2)}"
        while _pfile(pid).exists():
            pid = f"{base}-{secrets.token_hex(2)}"
        p = {"schema": SCHEMA, "id": pid, "name": name, "slug": base, "description": str(description or "").strip(),
             "status": "active", "created_at": D.now_iso(), "created_by": actor, "version": 0,
             "primary_folder": None, "folders": [], "instructions": str(instructions or ""),
             "webui_project_id": None, "dreamer": json.loads(json.dumps(DREAMER_DEFAULTS))}
        for f in folders or []:
            rec = folder_record(f)
            _check_folder_free(p, rec["path"], others)
            p["folders"].append(rec)
        if p["folders"]:
            want = str(canonical(primary_path)) if primary_path else p["folders"][0]["path"]
            p["primary_folder"] = next((f["id"] for f in p["folders"] if f["path"] == want), p["folders"][0]["id"])
        _validate(p)
        h = _write(p, actor, f"create project {name} ({pid})", created=True)
        sync_workspace(p)
    return {**p, "hash": h}


def mutate(pid: str, fn, *, expected_hash: str | None, actor: str = "user", message: str = "update project") -> dict:
    """Optimistic concurrency: read → check hash → apply fn(project, others) → validate → write + commit."""
    with store._lock("store"):
        p, h = load(pid)
        if expected_hash is not None and expected_hash != h:
            raise store.ConflictError(f"project {pid} changed since you loaded it; reload and retry", h)
        others = [o for o in all_projects() if o["id"] != pid]
        fn(p, others)
        _validate(p)
        nh = _write(p, actor, message)
        sync_workspace(p)  # inside the lock: overlapping edits can't leave stale symlinks behind
    return {**p, "hash": nh}


def update(pid: str, *, expected_hash: str | None, actor: str = "user", **fields) -> dict:
    allowed = {"name", "description", "instructions", "webui_project_id", "dreamer", "memory_keys"}
    bad = set(fields) - allowed
    if bad:
        raise ProjectError(f"can't change {', '.join(sorted(bad))}")

    def fn(p, others):
        if "name" in fields:
            n = re.sub(r"\s+", " ", str(fields["name"] or "")).strip()
            if any(str(o.get("name", "")).lower() == n.lower() for o in others):
                raise ProjectError(f"a project called “{n}” already exists")
            p["name"] = n
        for k in ("description", "instructions"):
            if k in fields:
                p[k] = str(fields[k] or "")
        if "webui_project_id" in fields:
            v = fields["webui_project_id"]
            if v is not None and not re.fullmatch(r"[0-9a-f]{6,32}", str(v)):
                raise ProjectError("bad webui project id")
            p["webui_project_id"] = v
        if "dreamer" in fields:
            d = fields["dreamer"] or {}
            cur = p.setdefault("dreamer", json.loads(json.dumps(DREAMER_DEFAULTS)))
            if "after_sessions" in d:
                cur["after_sessions"] = max(3, min(500, int(d["after_sessions"])))
        if "memory_keys" in fields:
            keys = [str(k) for k in fields["memory_keys"] or []]
            offered = {c["key"] for c in legacy_candidates(p)}  # archived projects keep their claims
            bad = [k for k in keys if k not in offered and k not in (p.get("memory_keys") or [])]
            if bad:
                raise ProjectError(f"not an available legacy memory folder: {', '.join(bad)}")
            p["memory_keys"] = sorted(set(keys))[:10]
    return mutate(pid, fn, expected_hash=expected_hash, actor=actor, message=f"update project {pid}: {', '.join(fields)}")


def add_folder(pid: str, path: str, *, expected_hash: str | None = None, primary_too: bool = False,
               actor: str = "user") -> dict:
    rec = folder_record(path)

    def fn(p, others):
        if p.get("status") != "active":
            raise ProjectError("unarchive the project first")
        _check_folder_free(p, rec["path"], [o for o in others if o.get("status") == "active"])
        p["folders"].append(rec)
        if primary_too or not p.get("primary_folder"):
            p["primary_folder"] = rec["id"]
    return mutate(pid, fn, expected_hash=expected_hash, actor=actor, message=f"project {pid}: add folder {tilde(rec['path'])}")


def remove_folder(pid: str, folder_id: str, *, expected_hash: str | None = None, actor: str = "user") -> dict:
    def fn(p, others):
        before = len(p["folders"])
        p["folders"] = [f for f in p["folders"] if f["id"] != folder_id]
        if len(p["folders"]) == before:
            raise ProjectError("no such folder in this project")
        if p.get("primary_folder") == folder_id:
            p["primary_folder"] = p["folders"][0]["id"] if p["folders"] else None
        p["folders_removed_at"] = D.now_iso()
    return mutate(pid, fn, expected_hash=expected_hash, actor=actor, message=f"project {pid}: remove folder {folder_id}")


def set_primary(pid: str, folder_id: str, *, expected_hash: str | None = None, actor: str = "user") -> dict:
    def fn(p, others):
        if not any(f["id"] == folder_id for f in p["folders"]):
            raise ProjectError("no such folder in this project")
        p["primary_folder"] = folder_id
    return mutate(pid, fn, expected_hash=expected_hash, actor=actor, message=f"project {pid}: primary {folder_id}")


def refresh_folders(pid: str, actor: str = "user") -> dict:
    """Re-read git/context metadata (folders that vanished stay listed, flagged missing)."""
    def fn(p, others):
        for f in p["folders"]:
            fp = Path(f["path"])
            f["missing"] = not fp.is_dir()
            if not f["missing"]:
                f["git"], f["context_files"] = _git_meta(fp), context_files(fp)
    return mutate(pid, fn, expected_hash=None, actor=actor, message=f"project {pid}: refresh folders")


def set_status(pid: str, status: str, *, expected_hash: str | None = None, actor: str = "user") -> dict:
    if status not in ("active", "archived"):
        raise ProjectError("bad status")

    def fn(p, others):
        if status == "active":
            for f in p["folders"]:  # its folders may have been attached elsewhere meanwhile
                _check_folder_free({"folders": []}, f["path"], [o for o in others if o.get("status") == "active"])
        p["status"] = status
    return mutate(pid, fn, expected_hash=expected_hash, actor=actor, message=f"project {pid}: {status}")


def delete(pid: str, actor: str = "user") -> dict:
    """Only archived projects. Files leave the tree but stay in git history (recoverable)."""
    from . import proposals as P
    lockf = store.root() / ".locks" / f"dream-{pid}.lock"
    if lockf.exists():
        import fcntl
        with open(lockf, "w") as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fh, fcntl.LOCK_UN)
            except BlockingIOError:
                raise ProjectError("the Dreamer is running for this project; try again when it finishes") from None
    for x in P.list_("pending"):
        if project_of_proposal(x) == pid:
            P.reject(x["proposal_id"], actor=actor, reason="project deleted")
    with store._lock("store"):
        p, _ = load(pid)
        if p.get("status") != "archived":
            raise ProjectError("archive the project before deleting it")
        shutil.rmtree(_pdir(pid))
        sha = store.commit([f"projects/{pid}"], actor, f"delete project {p['name']} ({pid})")
        store.append_event("project_deleted", id=pid, actor=actor, commit=sha)
        sync_workspace({"id": pid, "status": "deleted", "folders": []})
    return {"ok": True, "id": pid, "recover": f"git -C {store.root()} checkout {sha}^ -- projects/{pid}" if sha else None}


# ------------------------------------------------------------------ workspace (symlinks for the WebUI/Claude)

def workspace_path(pid: str) -> Path:
    return workspaces_dir() / pid


def webui_workspaces_file() -> Path:
    return Path(os.environ.get("HERMES_WEBUI_STATE_DIR") or Path.home() / ".hermes" / "webui") / "workspaces.json"


def label_webui_workspace(p: dict) -> bool:
    """The WebUI's folder picker lists this project's workspace under the project's name (not its id),
    and drops it once the project is archived or deleted. Only renames/removes entries already there."""
    f = webui_workspaces_file()
    try:
        ws = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(ws, list):
        return False
    path = str(workspaces_dir() / p["id"])
    mine = [w for w in ws if isinstance(w, dict) and str(w.get("path", "")).rstrip("/") == path]
    if not mine:
        return False  # never adds entries: the WebUI registers a project's workspace when it's first used
    if p.get("status") != "active":
        out = [w for w in ws if w not in mine]
    else:
        out = [{**w, "name": p["name"]} if w in mine else w for w in ws]
    if out == ws:
        return False
    tmp = f.with_suffix(f".{secrets.token_hex(4)}.tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, f)
    return True


def label_all_webui_workspaces() -> int:
    n = 0
    for p in all_projects(include_archived=True):
        try:
            n += label_webui_workspace(p)
        except OSError:
            pass
    return n


def sync_workspace(p: dict) -> None:
    """~/hermes/workspaces/<id>/<label> → each folder. Removed when the project is archived/deleted."""
    wdir = workspaces_dir() / p["id"]
    try:
        label_webui_workspace(p)
    except OSError:
        pass
    if p.get("status") != "active":
        if wdir.is_dir():
            for link in wdir.iterdir():
                if link.is_symlink():
                    link.unlink()
            try:
                wdir.rmdir()
            except OSError:
                pass
        return
    wdir.mkdir(parents=True, exist_ok=True)
    want, used = {}, set()
    for f in p.get("folders", []):
        name, n = f["label"] or "folder", 2
        while name in used:
            name, n = f"{f['label']}-{n}", n + 1
        used.add(name)
        want[name] = f["path"]
    for link in wdir.iterdir():
        if link.is_symlink() and (link.name not in want or os.readlink(link) != want[link.name]):
            link.unlink()
    for name, target in want.items():
        link = wdir / name
        if not link.exists() and not link.is_symlink():
            link.symlink_to(target, target_is_directory=True)


# ------------------------------------------------------------------ legacy memory (opt-in aliases, no moves)

def legacy_candidates(p: dict, others: list[dict] | None = None) -> list[dict]:
    """Pre-Projects memory folders (`projects/<key>/`) that look like they belong to this project:
    the key of one of its folders, not claimed by another project. The user ticks which to include;
    nothing is adopted automatically and no file moves (ids stay valid, history intact)."""
    from . import trace as T
    others = [o for o in (others if others is not None else all_projects(include_archived=True)) if o["id"] != p["id"]]
    claimed = {k for o in others for k in o.get("memory_keys") or []}
    keys = {}
    for f in p.get("folders", []):
        k = T.legacy_project_name(f["path"])
        exact = os.path.realpath(f["path"]) in (T._PROJECTS["map"] or {})
        direct = Path(f["path"]).parent == allowed_root()
        # a key like `developer` (every repo under ~/Developer) would pull in other repos' memory
        if k and not _ID.match(k) and k not in claimed and (exact or (direct and D.slug(k) == D.slug(f["label"]))):
            keys.setdefault(D.slug(k), f["label"])
    out = []
    for k, label in keys.items():
        d = store.root() / "projects" / k
        if d.is_dir() and not (d / "project.json").exists():
            n = sum(1 for _ in d.glob("*.md"))
            if n:
                out.append({"key": k, "memories": n, "folder": label})
    return out


def memory_keys(pid: str | None) -> list[str]:
    """All memory folder keys visible to a project: its id + the legacy keys the user included."""
    if not pid:
        return []
    p = get(pid)
    return [pid, *((p or {}).get("memory_keys") or [])]


def owner_of_key(key: str) -> str | None:
    if _ID.match(key or "") and get(key):
        return key
    return next((p["id"] for p in all_projects(include_archived=True) if key in (p.get("memory_keys") or [])), None)


def claim_history(pid: str, actor: str = "user") -> int:
    """Attach traces that belong to no Project yet and ran inside this project's folders. Never takes
    a trace from another project. Logged, so the move is auditable."""
    from . import trace as T
    p = get(pid)
    if not p:
        return 0
    n = 0
    owned = {x["id"] for x in all_projects(include_archived=True)} | {k for x in all_projects(include_archived=True)
                                                                      for k in x.get("memory_keys") or []}
    mine = [f["path"] for f in p.get("folders", [])]
    for f in sorted((store.root() / "sessions").glob("*/*/*/trace.json")):
        t = T._read_json(f)
        if not t or str(t.get("project") or "") in owned:
            continue
        if not any(str(c or "").startswith(m) for m in mine for c in [t.get("cwd"), *(t.get("files_modified") or [])[:50]]):
            continue  # cheap prefilter: never ran in this project's folders
        before = t.get("project")
        T.attribute(t)
        if t.get("project") == pid:
            with store._lock("traces"):
                cur = T._read_json(f)
                if not cur or str(cur.get("project") or "") in owned:
                    continue  # saved again meanwhile
                store._atomic_write(f, json.dumps(t, ensure_ascii=False, indent=1))
            n += 1
            store.append_event("trace_claimed", trace=t["id"], project=pid, previous=before, actor=actor)
    return n


# ------------------------------------------------------------------ summaries

def summary(p: dict) -> dict:
    """Card/header numbers for the UI (cheap: no trace parsing beyond the session index)."""
    from . import proposals as P
    pend = [x for x in P.list_("pending") if project_of_proposal(x) == p["id"]]
    keys = memory_keys(p["id"])
    mems = [m for m in store.all_memories() if m.id.startswith("projects/") and m.id.split("/")[1] in keys]
    return {"folders": len(p.get("folders", [])), "memories": len(mems), "pending": len(pend),
            "skills_proposed": sum(1 for x in pend if x.get("type") == "skill"),
            "skills": len(list((_pdir(p["id"]) / "skills").glob("*/SKILL.md")))}


def project_of_proposal(x: dict) -> str | None:
    t = x.get("target") or ""
    if t.startswith("projects/"):
        return owner_of_key(t.split("/")[1])
    if t.startswith("global/"):
        return None
    if x.get("project"):
        return x["project"]
    meta = (x.get("proposed_change") or {}).get("meta") or {}
    return owner_of_key(meta.get("project") or "")



# ------------------------------------------------------------------ worker context (progressive disclosure)

MAX_PROMPT_FOLDERS = 8
MAX_PROMPT_SKILLS = 8
INLINE_INSTRUCTIONS = 1200


def instructions_path(pid: str) -> Path:
    return _pdir(pid) / "INSTRUCTIONS.md"


def committed(path: Path, text: str | None = None) -> bool:
    """True if this store file on disk is exactly what git committed. Workers can't Edit the store, but
    a Bash write could; such a file must not reach a prompt (same rule as memories)."""
    try:
        rel = str(path.resolve().relative_to(store.root().resolve()))
        text = path.read_text() if text is None else text
    except (OSError, ValueError):
        return False
    return store.committed_blobs().get(rel) == store._blob_sha(text)


def skills(pid: str) -> list[dict]:
    """Approved project skills: memory/projects/<id>/skills/<name>/SKILL.md (loaded only for this project)."""
    out = []
    for f in sorted((_pdir(pid) / "skills").glob("*/SKILL.md")):
        text = f.read_text()
        if not committed(f, text):
            store.append_event("integrity_mismatch", ids=[str(f.relative_to(store.root()))])
            continue
        meta, _ = D.parse(text)
        out.append({"name": str(meta.get("name") or f.parent.name), "description": str(meta.get("description") or "")[:200],
                    "path": str(f)})
    return out


def context_block(p: dict, request_text: str = "") -> str:
    """What a worker learns about its project up front: identity, folders, instructions, skills.
    Short by construction; everything else is a path the worker can open if the task needs it."""
    from . import security
    trusted = committed(_pfile(p["id"]))
    if not trusted:
        store.append_event("integrity_mismatch", ids=[f"projects/{p['id']}/project.json"])
    lines = [f"PROJECT: {security.clean_title(p['name'])} (id {p['id']})"
             + (f" — {security.clean_title(p['description'], 160)}" if p.get("description") else ""),
             "These folders are ONE project; work across them as one system. You run in the primary one; "
             "the others are added directories (read/edit as the task needs)."]
    pf = primary(p)
    folders = sorted(p.get("folders", []), key=lambda f: f is not pf)
    low = (request_text or "").lower()
    for f in folders[:MAX_PROMPT_FOLDERS]:
        tags = (["primary"] if f is pf else []) + (["git"] if f.get("git") else [])
        if f.get("context_files"):
            tags.append("local context: " + ", ".join(f["context_files"][:4]))
        label = security.clean_title(f["label"], 60)
        named = " ← named in the request" if len(label) >= 4 and label.lower() in low else ""
        lines.append(f"- {label}: {security.clean_title(tilde(f['path']), 200)} [{security.clean_title('; '.join(tags), 300)}]{named}")
    if len(folders) > MAX_PROMPT_FOLDERS:
        lines.append(f"- … +{len(folders) - MAX_PROMPT_FOLDERS} more (`~/hermes/bin/hmem project show {p['id']}`)")
    lines.append("Each folder's own CLAUDE.md / .hermes/ files describe that repo; open the ones for folders you touch.")
    ins = (p.get("instructions") or "").strip()
    if ins and not trusted:
        lines.append("PROJECT INSTRUCTIONS withheld: the project file changed outside Hermes (`hmem doctor`).")
    elif ins:
        if len(ins) <= INLINE_INSTRUCTIONS:
            n = secrets.token_hex(3)
            lines += [f"<project-instructions nonce={n}> (written by the user for this project)", ins,
                      f"</project-instructions nonce={n}>"]
        else:
            lines.append(f"PROJECT INSTRUCTIONS (by the user, {len(ins)} chars): read {instructions_path(p['id'])} first.")
    sk = skills(p["id"])
    if sk:
        lines.append("PROJECT SKILLS (read the SKILL.md only when it fits the task):")
        lines += [f"- {s['name']} — {security.clean_title(s['description'])[:140]} ({tilde(s['path'])})"
                  for s in sk[:MAX_PROMPT_SKILLS]]
    return "\n".join(lines)
