"""Current-project tracking.

Resolution order for a request:
  1. explicit marker in the message: `@project:<name>` / `in project <name>` / `[<name>]`
  2. the session workspace (WebUI picker / CLI dir): the General workspace means generic chat
     (no project); a dir inside a known project selects that project
  3. exactly one known project name mentioned in the message (word match, name >= 4 chars)
  4. the remembered current project
  5. the session's working directory, when it is a real folder (not ~)
  6. none -> generic chat, or the hub asks which project if the request needs one
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from .common import STATE_DIR, load_projects_file, projects_file_lock, read_json, save_projects_file, write_json

CURRENT_FILE = STATE_DIR / "current_project.json"
GENERAL_DIR = Path.home() / "hermes" / "general"  # WebUI "General" workspace = no project
WORKSPACES_DIR = Path.home() / "hermes" / "workspaces"  # multi-folder workspaces (linked folders)
_DISCOVERY_CACHE = STATE_DIR / "discovered_projects.json"
_SCAN_ROOTS = [Path.home(), Path.home() / "Developer", Path.home() / "Projects", Path.home() / "code"]


def _discover() -> dict[str, str]:
    cached = read_json(_DISCOVERY_CACHE, None)
    if cached and time.time() - cached.get("at", 0) < 6 * 3600:
        return cached["projects"]
    found: dict[str, str] = {}
    for root in _SCAN_ROOTS:
        if not root.is_dir():
            continue
        try:
            for child in root.iterdir():
                if child.is_dir() and not child.name.startswith(".") and (child / ".git").exists():
                    found.setdefault(child.name.lower(), str(child))
        except PermissionError:
            continue
    write_json(_DISCOVERY_CACHE, {"at": time.time(), "projects": found})
    return found


def workspace_defs() -> dict[str, dict]:
    """Multi-folder workspaces: {name: {"folders": [...], "primary": path}} from projects.yaml."""
    out = {}
    for name, d in (load_projects_file().get("workspaces") or {}).items():
        folders = [str(Path(str(f)).expanduser()) for f in (d or {}).get("folders", [])]
        if folders:
            primary = str(Path(str(d.get("primary") or folders[0])).expanduser())
            out[str(name).lower()] = {"folders": folders, "primary": primary}
    return out


def known_projects() -> dict[str, str]:
    """name -> path. A multi-folder workspace maps to its ~/hermes/workspaces/<name> folder."""
    projects = dict(_discover())
    for name, path in (load_projects_file().get("projects") or {}).items():
        projects[str(name).lower()] = str(Path(str(path)).expanduser())
    for name in workspace_defs():
        projects[name] = str(WORKSPACES_DIR / name)
    return projects


def folders_for(path: str | None) -> tuple[str | None, list[str]]:
    """(primary folder Claude runs in, extra folders it may also edit) for a resolved project path."""
    if not path:
        return None, []
    p = Path(path).expanduser().absolute()
    if _within(p, WORKSPACES_DIR):
        name = p.relative_to(WORKSPACES_DIR).parts[0].lower() if p != WORKSPACES_DIR else ""
        ws = workspace_defs().get(name)
        if ws:
            return ws["primary"], [f for f in ws["folders"] if f != ws["primary"]]
    return path, []


def create_workspace(name: str, folders: list[str], primary: str | None = None) -> tuple[bool, str]:
    """Create/replace a multi-folder workspace: config entry, linked folder for the WebUI picker,
    WebUI workspace entry, and a native `hermes project` mirror (best effort)."""
    slug = re.sub(r"[^a-z0-9._-]+", "-", name.lower()).strip("-")
    paths = []
    for f in folders:
        fp = Path(f).expanduser()
        if not fp.is_dir():
            match = known_projects().get(f.lower())
            fp = Path(match) if match else fp
        if not fp.is_dir():
            return False, f"Not a folder or known project: {f}"
        paths.append(str(fp.resolve()))
    if not slug or not paths:
        return False, "Need a name and at least one folder."
    primary_path = str(Path(primary).expanduser().resolve()) if primary else paths[0]
    with projects_file_lock():
        data = load_projects_file()
        data.setdefault("workspaces", {})[slug] = {
            "folders": [p.replace(str(Path.home()), "~", 1) for p in paths],
            "primary": primary_path.replace(str(Path.home()), "~", 1)}
        save_projects_file(data)
    wdir = WORKSPACES_DIR / slug
    wdir.mkdir(parents=True, exist_ok=True)
    for link in wdir.iterdir():
        if link.is_symlink():
            link.unlink()
    for path in paths:
        link = wdir / Path(path).name
        n = 2
        while link.exists() or link.is_symlink():
            link = wdir / f"{Path(path).name}-{n}"
            n += 1
        link.symlink_to(path, target_is_directory=True)
    _add_webui_workspace(str(wdir), f"{name} ({len(paths)} folders)")
    _mirror_hermes_project(slug, paths, primary_path)
    return True, f"Workspace '{slug}': primary {primary_path}; also " + (", ".join(p for p in paths if p != primary_path) or "nothing else")


def _add_webui_workspace(path: str, label: str) -> None:
    f = Path.home() / ".hermes" / "webui" / "workspaces.json"
    ws = read_json(f, []) or []
    if not any(Path(w.get("path", "")).expanduser() == Path(path) for w in ws):
        ws.append({"path": path, "name": label})
        write_json(f, ws)


def _mirror_hermes_project(slug: str, paths: list[str], primary: str) -> None:
    import shutil
    import subprocess
    exe = shutil.which("hermes") or str(Path.home() / ".local" / "bin" / "hermes")
    run = lambda *a: subprocess.run([exe, "project", *a], capture_output=True, timeout=60)  # noqa: E731
    try:
        run("create", slug, primary)
        for p in paths:
            if p != primary:
                run("add-folder", slug, p)
    except Exception:
        pass  # the command-centre config above is the source of truth


def current() -> str | None:
    p = (read_json(CURRENT_FILE, {}) or {}).get("path")
    return p if p and Path(p).is_dir() else None


def set_current(name_or_path: str) -> tuple[str | None, str]:
    """Resolve and remember a project. Returns (path, message)."""
    target = name_or_path.strip().strip("\"'")
    projects = known_projects()
    path = None
    if target.lower() in projects:
        path = projects[target.lower()]
    else:
        p = Path(target).expanduser()
        if p.is_dir():
            path = str(p.resolve())
            with projects_file_lock():
                data = load_projects_file()
                data.setdefault("projects", {})
                data["projects"].setdefault(p.resolve().name.lower(), path)
                save_projects_file(data)
    if not path:
        close = [n for n in projects if target.lower() in n][:8]
        hint = f" Close matches: {', '.join(close)}." if close else ""
        return None, f"Unknown project '{target}'.{hint}"
    write_json(CURRENT_FILE, {"path": path, "set_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    return path, f"Current project: {path}"


_EXPLICIT = [
    re.compile(r"@project:(\S+)", re.I),
    re.compile(r"\bin (?:the )?project\s+[\"']?([\w.\-~/]+)", re.I),
    re.compile(r"^\s*\[([\w.\-]+)\]"),
]


_NOT_PROJECTS = {Path(p).resolve() for p in ("/", "/tmp", "/private/tmp", str(Path.home()),
                                             str(Path.home() / ".hermes" / "hermes-agent"))}


def _within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def resolve(text: str, session_cwd: str | None) -> tuple[str | None, str]:
    """Returns (project_path | None, source). source "general" = the user chose generic chat."""
    projects = known_projects()
    for rx in _EXPLICIT:
        m = rx.search(text)
        if m:
            path, _ = set_current(m.group(1))
            if path:
                return path, "explicit"
    # The session's workspace (WebUI workspace picker / CLI launch dir) is the user's selection.
    if session_cwd:
        raw = Path(session_cwd).expanduser().absolute()
        if _within(raw, GENERAL_DIR):
            return None, "general"
        if _within(raw, WORKSPACES_DIR) and raw != WORKSPACES_DIR:
            ws = WORKSPACES_DIR / raw.relative_to(WORKSPACES_DIR).parts[0]
            if not ws.is_dir():  # an archived/deleted Project's workspace: ask, never run in an empty folder
                return None, "workspace no longer exists"
            return str(ws), "session workspace"
        cwd = raw.resolve()
        for path in sorted(projects.values(), key=len, reverse=True):
            if _within(cwd, Path(path).resolve()):
                return path, "session workspace"
        if cwd.is_dir() and cwd not in _NOT_PROJECTS:
            return str(cwd), "session workspace"
    # Ignore file paths and @refs: "/Users/me/hermes/x.png" must not select project "hermes".
    low = re.sub(r"@\w+:(\"[^\"]*\"|'[^']*'|\S+)|(~|/)[^\s]*", " ", text).lower()
    mentioned = {path for name, path in projects.items()
                 if len(name) >= 4 and re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", low)}
    if len(mentioned) == 1:
        path = mentioned.pop()
        write_json(CURRENT_FILE, {"path": path, "set_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
        return path, "inferred from message"
    cur = current()
    if cur:
        return cur, "current project"
    return None, "unresolved"


WEBUI_STATE = Path.home() / ".hermes" / "webui"


def webui_project_spaces(session_id: str) -> tuple[str | None, list[str]]:
    """WebUI 'project' of this session -> (default space, all its spaces). Empty if none."""
    sess = read_json(WEBUI_STATE / "sessions" / f"{session_id}.json", {}) or {}
    pid = sess.get("project_id")
    if not pid:
        return None, []
    for p in read_json(WEBUI_STATE / "projects.json", []) or []:
        if p.get("project_id") == pid:
            spaces = [str(Path(w).expanduser()) for w in (p.get("workspaces") or [])]
            if not spaces and p.get("workspace"):
                spaces = [str(Path(p["workspace"]).expanduser())]
            default = p.get("default_workspace") or (spaces[0] if spaces else None)
            return default, [s for s in spaces if Path(s).is_dir()]
    return None, []


def expand_with_project(session_id: str, primary: str | None, extras: list[str]) -> tuple[str | None, list[str]]:
    """Add the other spaces of the session's WebUI project (each may itself be a multi-folder workspace)."""
    default, spaces = webui_project_spaces(session_id)
    if not spaces:
        return primary, extras
    general = GENERAL_DIR.resolve()
    if primary is None:
        primary, extras = folders_for(default)
    seen = {str(Path(primary).resolve())} if primary else set()
    out = list(extras)
    for sp in spaces:
        p0, ex = folders_for(sp)
        for f in [p0, *ex]:
            if f and Path(f).resolve() != general and str(Path(f).resolve()) not in seen:
                seen.add(str(Path(f).resolve()))
                out.append(f)
    return primary, out


# --------------------------------------------------------------------------- first-class Projects (hmem registry)

def _registry():
    import sys
    home = str(Path(__file__).resolve().parents[2])
    if home not in sys.path:
        sys.path.insert(0, home)
    from hmem import projects as registry
    return registry


def registry_for(text: str, session_cwd: str | None) -> dict | None:
    """The Hermes Project this request belongs to: an explicit `@project:<name>` marker, else the
    session's workspace (the project's workspace dir or any of its linked folders)."""
    try:
        reg = _registry()
        pid = reg.project_for_path(session_cwd) if session_cwd else None
        session = reg.get(pid) if pid else None
        # Inside a Project's session only a leading `@project:` switches; prose like "in project x" or a
        # pasted "[x]" stays text. Outside one, `@project:` anywhere names a Project.
        m = re.match(r"\s*@project:(\S+)", text or "", re.I) or (None if session else _EXPLICIT[0].search(text or ""))
        if m:
            p = reg.find(m.group(1))
            if p and p.get("status") == "active":
                return p
            if m.group(1).lower() in {k.lower() for k in known_projects()}:
                return None  # an older (non-registry) project named explicitly: the folder rules handle it
        if session and session.get("status") == "active":
            return session
    except Exception:
        return None  # the registry must never block routing; fall back to the folder rules
    return None


def registry_folders(p: dict) -> tuple[str | None, list[str]]:
    """(primary, others) for a Project, each re-checked now (a swapped symlink or vanished folder is dropped)."""
    reg = _registry()
    ok = [f for f in p.get("folders", []) if reg.folder_ok(f["path"])]
    pf = reg.primary({**p, "folders": ok})
    if not pf:
        return None, []
    return pf["path"], [f["path"] for f in ok if f is not pf]
