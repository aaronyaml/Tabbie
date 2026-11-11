"""Repo suggestions for the Projects folder picker / New project sheet (read-only).

`suggest(q)` lists git repos under the usual code folders that no project has claimed yet.
With no query: most recently active first. With a query (the project name being typed): ranked by how
well the folder name, package.json name, git remote or README title match it.

Kept cheap enough to call per keystroke: one bounded scan (home + common code folders, depth 2, capped
entries per folder) is cached for a minute, and per-repo metadata is read from a handful of small files
(no git subprocess, no recursive walk). Nothing returned contains file contents beyond a name/title.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

from . import projects

CODE_DIRS = ("Developer", "Projects", "projects", "code", "Code", "src", "dev", "repos", "git", "GitHub", "github",
             "work", "Work", "Sites", "workspace", "Workspace")
_SKIP = {"Library", "Desktop", "Documents", "Downloads", "Movies", "Music", "Pictures", "Public", "Applications",
         "node_modules", "venv", "Pictures Library",
         # Windows' own home folders
         "AppData", "OneDrive", "Videos", "Saved Games", "Contacts", "Favorites", "Links", "Searches", "3D Objects",
         "Application Data", "Local Settings", "NetHood", "PrintHood", "Recent", "SendTo", "Start Menu", "Templates"}
MAX_ENTRIES = 300   # children looked at per folder
MAX_REPOS = 600     # repos kept from one scan
TTL = 60.0          # seconds a scan is reused

_LOCK = threading.Lock()
_CACHE: dict = {"at": 0.0, "base": None, "repos": []}


# ------------------------------------------------------------------ scan

def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def _read(p: Path, limit: int = 8192) -> str:
    try:
        if p.stat().st_size > 512_000:
            return ""
        with open(p, encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except OSError:
        return ""


_REMOTE = re.compile(r'\[remote "origin"\][^\[]*?\burl\s*=\s*(\S+)', re.S)


def clean_remote(url: str) -> str:
    """host/owner/repo with any user:token@ dropped (same shape as projects._git_meta)."""
    url = re.sub(r"^[a-z+]+://", "", url.strip())
    url = re.sub(r"^[^@/]+@", "", url).replace(":", "/", 1) if url else ""
    return url.removesuffix("/").removesuffix(".git")[:200]


def _meta(d: Path) -> dict:
    git = d / ".git"
    remote = ""
    if git.is_dir():
        m = _REMOTE.search(_read(git / "config", 16384))
        remote = clean_remote(m.group(1)) if m else ""
        active = max(_mtime(git / "logs" / "HEAD"), _mtime(git / "index"), _mtime(git / "HEAD"), _mtime(d))
    else:  # a worktree / submodule: .git is a file
        active = max(_mtime(git), _mtime(d))
    pkg = ""
    raw = _read(d / "package.json", 65536)
    if raw:
        try:
            v = json.loads(raw).get("name")
            pkg = v[:80] if isinstance(v, str) else ""
        except (ValueError, AttributeError):
            pkg = ""
    title = ""
    for name in ("README.md", "readme.md", "README.MD", "Readme.md", "README"):
        txt = _read(d / name, 4096)
        if txt:
            for line in txt.splitlines()[:40]:
                s = line.strip()
                if s.startswith("# "):
                    title = re.sub(r"[`*_\[\]<>]", "", s[2:]).strip()[:80]
                    break
            break
    return {"name": d.name, "path": str(d), "display": projects.tilde(str(d)), "modified": int(active),
            "remote": remote, "package": pkg, "readme": title}


def _kids(d: Path) -> list[os.DirEntry]:
    out = []
    try:
        with os.scandir(d) as it:
            for i, e in enumerate(it):
                if i >= MAX_ENTRIES * 3:
                    break
                if e.name.startswith(".") or e.name in _SKIP:
                    continue
                try:
                    if e.is_dir():
                        out.append(e)
                except OSError:
                    continue
                if len(out) >= MAX_ENTRIES:
                    break
    except OSError:
        pass
    return out


def scan(base: Path | None = None) -> list[dict]:
    """Every git repo at depth 1-2 under home and the common code folders (bounded), with metadata."""
    base = base or projects.allowed_root()
    # code folders first, so a repo two levels inside ~/Developer is reached at depth 2 from there
    # (home's own walk then skips ~/Developer as already seen)
    roots: list[Path] = []
    for c in [base / c for c in CODE_DIRS if (base / c).is_dir()] + [base]:
        try:
            real = projects.true_case(c.resolve())  # APFS: ~/Projects and ~/projects are one folder
        except OSError:
            continue
        if real not in roots:
            roots.append(real)
    seen: set[str] = {str(r) for r in roots}
    out: list[dict] = []

    def visit(d: Path, depth: int):
        if len(out) >= MAX_REPOS:
            return
        for e in _kids(d):
            c = Path(e.path)
            try:
                real = c.resolve()
            except OSError:
                continue
            if str(real) in seen:
                continue
            if (c / ".git").exists():
                seen.add(str(real))
                if projects._forbidden(real) is None:
                    m = _meta(real)
                    m["name"] = c.name
                    out.append(m)
            elif depth < 2 and not e.is_symlink():
                seen.add(str(real))
                visit(c, depth + 1)
            if len(out) >= MAX_REPOS:
                return

    for r in roots:
        visit(r, 1)
    return out


def cached_scan(force: bool = False) -> list[dict]:
    base = projects.allowed_root()
    with _LOCK:
        if not force and _CACHE["base"] == str(base) and time.time() - _CACHE["at"] < TTL:
            return _CACHE["repos"]
    repos = scan(base)
    with _LOCK:
        _CACHE.update(at=time.time(), base=str(base), repos=repos)
    return repos


# ------------------------------------------------------------------ ranking

_SPLIT = re.compile(r"[^a-z0-9]+")
_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])")


def _tokens(s: str) -> list[str]:
    return [t for t in _SPLIT.split(_CAMEL.sub(" ", s or "").lower()) if t]


def _subseq(q: str, s: str) -> float:
    """q's letters in order inside s → density (0..1); 0 if not a subsequence."""
    i, first, last = 0, -1, -1
    for j, ch in enumerate(s):
        if i < len(q) and ch == q[i]:
            if first < 0:
                first = j
            last = j
            i += 1
    if i < len(q):
        return 0.0
    return len(q) / (last - first + 1)


def field_score(q: str, field: str) -> float:
    """0-100: how well the typed name q matches one field (folder name, package name, …)."""
    qt, ft = _tokens(q), _tokens(field)
    if not qt or not ft:
        return 0.0
    qc, fc = "".join(qt), "".join(ft)
    if qc == fc:
        return 100.0
    if fc.startswith(qc) or (qc.startswith(fc) and len(fc) >= 4):
        return 85.0
    if len(qc) >= 3 and qc in fc:
        return 70.0
    # word level: each query word found at the start of a field word (or the other way round, for plurals)
    def hit(a: str) -> bool:
        return any(b.startswith(a) or (len(b) >= 4 and a.startswith(b)) or (len(a) >= 4 and a in b) for b in ft)
    words = [a for a in qt if len(a) >= 2]
    got = sum(1 for a in words if hit(a))
    if words and got == len(words):
        return 60.0
    if got:
        return 25.0 + 30.0 * got / len(words)
    if len(qc) >= 3:
        d = _subseq(qc, fc)
        if d >= 0.5:
            return 15.0 + 25.0 * d
    return 0.0


_FIELDS = (("name", 1.0, None), ("package", 0.9, "package.json"), ("remote", 0.85, "remote"),
           ("readme", 0.8, "README"))


def score(q: str, r: dict) -> tuple[float, str | None]:
    best, why = 0.0, None
    for k, w, label in _FIELDS:
        v = r.get(k) or ""
        if k == "remote":
            v = v.rsplit("/", 1)[-1]  # the repo name; the host/owner would match everything
        s = field_score(q, v) * w
        if s > best:
            best, why = s, (f"{label}: {v}" if label else None)
    return best, why


def suggest(q: str = "", taken: set[str] | None = None, limit: int = 60) -> dict:
    q = (q or "").strip()[:80]
    taken = taken if taken is not None else {f["path"] for pj in projects.all_projects() for f in pj.get("folders", [])}
    repos = [r for r in cached_scan() if r["path"] not in taken]
    now = time.time()

    def row(r, s=None, why=None):
        x = {"name": r["name"], "path": r["path"], "display": r["display"], "modified": r["modified"]}
        if s is not None:
            x["score"] = round(s, 1)
        if why:
            x["why"] = why[:60]
        return x

    recent = sorted(repos, key=lambda r: -r["modified"])
    if not _tokens(q):
        return {"query": "", "matched": False, "repos": [row(r) for r in recent[:limit]]}
    hits = []
    for r in repos:
        s, why = score(q, r)
        if s >= 20:
            age_days = max(0.0, (now - r["modified"]) / 86400)
            hits.append((s + 5.0 / (1 + age_days / 7), why, r))  # recency only breaks near-ties
    hits.sort(key=lambda x: -x[0])
    if not hits:  # nothing like the name: still show something useful
        return {"query": q, "matched": False, "repos": [row(r) for r in recent[:limit]]}
    return {"query": q, "matched": True, "repos": [row(r, s, why) for s, why, r in hits[:limit]]}
