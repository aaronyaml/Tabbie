"""Team projects: one person's Tabbie project (the main copy) set up on every teammate's Mac by itself.

Publishing Mac (whose project it is): each project it shares with the team (Settings → Team → shared projects)
that it didn't itself receive goes out as a recipe in the index room, a "status" event with kind "project":
name, description, instructions, colours, its GitHub repos (folder name, repo, branch, which one is primary) and
the project's memories (its rules and facts). Code travels through GitHub only, never through the relay; folders
with no GitHub remote stay on the publishing Mac.

Receiving Mac (everyone else), for a recipe signed by the publisher's own Mac (pinned by fingerprint; a browser
can't send one):
  1. clones each repo into ~/Tabbie Team/<Project>/<folder>, or uses a clone this Mac already has of that repo
  2. creates the Tabbie project with those folders (or adds them to its own project of the same name), its
     WebUI project, and shares it with the team, so chats in it show on the team site and the site can start them
  3. copies the project's memories in (each marked with where it came from; this Mac's own are never touched)
  4. keeps the clones current: fetches every few minutes and fast-forwards a clean checkout, and right before a
     chat from the team site runs in the project
Its instructions say how two people change one codebase: pull first, work on your own branch, push, pull request.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hmem import doc as D
from hmem import projects as P
from hmem import store

BASE = Path(os.environ.get("TT_PROJECTS_DIR") or Path.home() / "Tabbie Team")
REPO_RX = re.compile(r"^github\.com/([A-Za-z0-9_.-]{1,100})/([A-Za-z0-9_.-]{1,100})$")
BRANCH_RX = re.compile(r"^(?!-)(?!.*\.\.)[A-Za-z0-9._/-]{1,100}$")
NAME_RX = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}$")
MAX_MEMORIES = 200
MAX_RECIPE = 200_000  # bytes of JSON: well inside one relay event
SYNC_EVERY = 5 * 60
PUBLISH_EVERY = 10 * 60
GIT_FLAGS = ["-c", "core.hooksPath=/dev/null", "-c", "protocol.file.allow=never"]  # no repo hooks, no local paths
GIT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_SSH_COMMAND": "ssh -o BatchMode=yes", "GCM_INTERACTIVE": "never"}


# ---------- git ----------
def git(*args: str, cwd: str | Path | None = None, timeout: float = 60) -> tuple[int, str]:
    try:
        r = subprocess.run(["git", *GIT_FLAGS, *args],
                           cwd=cwd, check=False, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, env={**os.environ, **GIT_ENV})
        return r.returncode, (r.stdout + r.stderr).strip()
    except (OSError, subprocess.SubprocessError) as e:
        return 1, type(e).__name__


def remote_of(path: str | Path) -> str:
    """github.com/Owner/Repo for a folder's origin, or "" (any URL form: https, ssh, with or without .git)."""
    code, out = git("config", "--get", "remote.origin.url", cwd=path, timeout=5)
    if code or not out:
        return ""
    u = re.sub(r"^[a-z+]+://", "", out.strip())
    u = re.sub(r"^[^@/]+@", "", u).replace(":", "/", 1).removesuffix(".git").rstrip("/")
    return u if REPO_RX.match(u) else ""


def branch_of(path: str | Path) -> str:
    code, out = git("rev-parse", "--abbrev-ref", "HEAD", cwd=path, timeout=5)
    return out if code == 0 and BRANCH_RX.match(out or "") and out != "HEAD" else ""


# ---------- publishing ----------
def _memories(pid: str) -> list[dict]:
    blobs = store.committed_blobs()
    out = []
    for m in store.all_memories():
        meta = m.meta
        if meta.get("scope") != "project" or meta.get("project") != pid or meta.get("team_source"):
            continue  # only this Mac's own memories travel on, never ones it received
        if not store.is_committed(m, blobs):
            continue
        name = str(meta["id"]).rsplit("/", 1)[-1]
        if not NAME_RX.match(name):
            continue
        out.append({"name": name, "title": str(meta.get("title") or "")[:200], "type": str(meta.get("type") or "fact"),
                    "description": str(meta.get("description") or "")[:300], "tags": [str(t)[:40] for t in (meta.get("tags") or [])][:10],
                    "updated": str(meta.get("updated_at") or ""), "body": m.body.strip()[:D.MAX_BODY]})
    out.sort(key=lambda x: x["updated"], reverse=True)
    return out[:MAX_MEMORIES]


def recipe(p: dict) -> dict | None:
    """What a teammate's Mac needs to set this project up. None if it has no GitHub repos (nothing to share)."""
    repos = []
    prim = P.primary(p)
    for f in p.get("folders", []):
        if not P.folder_ok(f["path"]):
            continue
        r = remote_of(f["path"])
        if not r:
            continue
        repos.append({"label": f["label"], "repo": r, "branch": branch_of(f["path"]), "primary": f is prim})
    if not repos:
        return None
    if not any(x["primary"] for x in repos):
        repos[0]["primary"] = True
    out = {"kind": "project", "v": 1, "name": p["name"], "description": p.get("description") or "",
           "instructions": p.get("instructions") or "", "colours": p.get("colours") or [], "repos": repos,
           "memories": _memories(p["id"])}
    while len(json.dumps(out)) > MAX_RECIPE and out["memories"]:
        out["memories"].pop()  # oldest go first
    return out


def publishable(tt) -> list[dict]:
    """Recipes for this Mac's shared projects that it owns (not ones it received from a teammate)."""
    names = {n.lower() for n in tt.config().get("projects", [])}
    received = {f.get("pid") for f in tt.load("follow.json", {}).values()}
    out = []
    for p in P.all_projects():
        if p.get("status") == "active" and p["name"].lower() in names and p["id"] not in received:
            r = recipe(p)
            if r:
                out.append(r)
    return out


def due_recipes(tt) -> list[dict]:
    """Recipes that changed since this Mac last sent them (sent again after a day in any case)."""
    sent = tt.load("published.json", {})
    out = []
    for r in publishable(tt):
        h = hashlib.sha256(json.dumps(r, sort_keys=True).encode()).hexdigest()[:24]
        last = sent.get(r["name"].lower()) or {}
        if last.get("hash") != h or time.time() - last.get("at", 0) > 86400:
            out.append(dict(r, hash=h))
    return out


def mark_published(tt, r: dict):
    sent = tt.load("published.json", {})
    sent[r["name"].lower()] = {"hash": r["hash"], "at": time.time()}
    tt.save("published.json", sent)


# ---------- receiving ----------
def valid(r: dict) -> str | None:
    """Why a recipe can't be used (None = fine)."""
    if not isinstance(r, dict) or r.get("kind") != "project":
        return "not a project"
    name = str(r.get("name") or "").strip()
    if not name or len(name) > 80 or not P.safe_name(name):
        return "bad project name"
    repos = r.get("repos")
    if not isinstance(repos, list) or not repos or len(repos) > 20:
        return "no repos"
    labels = set()
    for x in repos:
        if not isinstance(x, dict) or not REPO_RX.match(str(x.get("repo") or "")):
            return "bad repo"
        lab = str(x.get("label") or "")
        if not P.safe_name(lab) or "/" in lab or lab.startswith(".") or lab.lower() in labels:
            return "bad folder name"
        labels.add(lab.lower())
        if x.get("branch") and not BRANCH_RX.match(str(x["branch"])):
            return "bad branch"
    if not isinstance(r.get("memories", []), list):
        return "bad memories"
    return None


def team_rule(owner: str, me: str) -> str:
    return (f"This project comes from {owner.title()}'s Tabbie, the main copy, and you both change its code. "
            f"Before changing a repo, pull it (git pull --ff-only). Make your changes on a new branch named "
            f"{me}/<short-topic>, commit, push it and open a pull request into the branch the repo normally ships "
            f"from (gh pr create if gh is installed, else give the GitHub compare link). Never push straight to a "
            f"branch other people work on. Finish by giving the user the pull request link.")


def _local_clone(repo: str) -> str | None:
    """A clone of this repo that's already on this Mac (in a usual place), so it isn't cloned twice."""
    for base in (Path.home(), Path.home() / "Developer", Path.home() / "Projects", Path.home() / "code"):
        try:
            kids = list(base.iterdir())
        except OSError:
            continue
        for d in kids:
            if d.is_dir() and not d.name.startswith(".") and (d / ".git").exists() and remote_of(d) == repo:
                return str(d.resolve())
    return None


def clone_urls(repo: str) -> list[str]:
    owner, name = REPO_RX.match(repo).groups()
    return [f"https://github.com/{owner}/{name}.git", f"git@github.com:{owner}/{name}.git"]


def clone(x: dict, dest: Path) -> tuple[str | None, str]:
    """(path, problem). Tries https, then ssh, so either kind of GitHub sign-in on this Mac works."""
    if (dest / ".git").exists() and remote_of(dest) == x["repo"]:
        return str(dest.resolve()), ""
    if dest.exists() and any(dest.iterdir()):
        return None, f"{dest} already has other files in it, so it can’t be downloaded there"
    dest.parent.mkdir(parents=True, exist_ok=True)
    last = ""
    for url in clone_urls(x["repo"]):
        code, out = git("clone", "--no-recurse-submodules", "--", url, str(dest), timeout=900)
        if code == 0:
            if x.get("branch"):
                b = x["branch"]
                if git("rev-parse", "--verify", "-q", f"refs/remotes/origin/{b}", cwd=dest, timeout=10)[0] == 0:
                    git("checkout", "-q", b, cwd=dest, timeout=60)
            return str(dest.resolve()), ""
        last = out
        if dest.exists() and not any(dest.iterdir()):
            dest.rmdir()
    if re.search(r"not found|Repository not found|Permission denied|could not read Username|Authentication failed", last, re.IGNORECASE):
        return None, f"This Mac’s GitHub sign-in can't open {x['repo']} (ask for access, then sign in with: gh auth login)"
    return None, f"Couldn’t download {x['repo']}: {last.splitlines()[-1][:160] if last else 'git failed'}"


def freshen(path: str) -> str:
    """Fetch, and fast-forward the checked-out branch if it's clean and behind. Returns a short state."""
    code, _ = git("fetch", "--prune", "-q", "origin", cwd=path, timeout=120)
    if code:
        return "couldn't fetch"
    if git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", cwd=path, timeout=5)[0]:
        return "no upstream"
    if git("status", "--porcelain", "--untracked-files=no", cwd=path, timeout=10)[1]:
        return "local changes"
    code, _out = git("merge", "--ff-only", "-q", "@{u}", cwd=path, timeout=60)
    return "current" if code == 0 else "diverged"


def _apply_memories(pid: str, owner: str, mems: list[dict]) -> int:
    actor = f"team:{owner}"
    have = {m.meta.get("team_source"): m for m in store.all_memories(include_inactive=True)
            if m.meta.get("project") == pid and str(m.meta.get("team_source") or "").startswith(owner + ":")}
    keep, changed = set(), 0
    for x in mems[:MAX_MEMORIES]:
        if not isinstance(x, dict) or not NAME_RX.match(str(x.get("name") or "")) or not str(x.get("body") or "").strip():
            continue
        src = f"{owner}:{x['name']}"
        keep.add(src)
        meta = {"id": f"projects/{pid}/{x['name']}", "scope": "project", "project": pid,
                "title": str(x.get("title") or x["name"])[:200], "type": x.get("type") if x.get("type") in D.TYPES else "fact",
                "description": f"From {owner.title()}'s copy of this project", "tags": [str(t)[:40] for t in (x.get("tags") or [])][:10],
                "status": "active", "trust": "agent", "team_source": src, "approved_by": f"team:{owner}"}
        body = str(x["body"])[:D.MAX_BODY]
        cur = have.get(src)
        try:
            if cur is None:
                if store.read(meta["id"]):
                    continue  # this Mac has its own memory with that name: keep it
                store.write(meta, body, actor=actor, create_only=True, reason=f"from {owner}'s Mac")
                changed += 1
            elif cur.body.strip() != body.strip() or cur.meta.get("title") != meta["title"] or cur.meta.get("status") != "active":
                if cur.meta.get("status") == "archived":
                    continue  # archived here: leave it
                meta["id"] = cur.meta["id"]
                store.write(meta, body, actor=actor, expected_hash=cur.hash, reason=f"updated on {owner}'s Mac")
                changed += 1
        except store.MemoryError_ as e:
            print(f"team projects: skipped a memory ({type(e).__name__}: {str(e)[:120]})", flush=True)
    for src, m in have.items():
        if src not in keep and m.meta.get("status") == "active":
            try:
                store.set_status(m.meta["id"], "archived", actor=actor, reason=f"removed on {owner}'s Mac")
                changed += 1
            except store.MemoryError_:
                pass
    return changed


class Follower:
    """Sets up and keeps current the projects teammates publish (runs in the daemon, beside the mirror)."""

    def __init__(self, tt, owner: str, webui=None):
        self.tt, self.me, self.webui = tt, owner, webui
        self.latest: dict[str, tuple[int, str, dict]] = {}  # key -> (ts, author, recipe), newest only
        self.wake = asyncio.Event()

    def offer(self, e: dict, payload: dict):
        """A verified "status" event from the index room. Only a teammate's own Mac may publish a project."""
        author = e.get("author")
        if author == self.me or payload.get("kind") != "project":
            return
        mac = ((self.tt.load("trust.json") or {}).get("macs") or {}).get(author) or {}
        if not mac or e.get("kid") != mac.get("kid"):
            return
        if valid(payload):
            return
        key = f"{author}/{payload['name'].lower()}"
        ts = int(e.get("ts") or 0)
        if key not in self.latest or ts >= self.latest[key][0]:
            self.latest[key] = (ts, author, payload)
            self.wake.set()

    # ---- the slow part, in a thread ----
    def apply(self, key: str, author: str, r: dict) -> dict:
        follow = self.tt.load("follow.json", {})
        st = follow.get(key, {})
        h = hashlib.sha256(json.dumps(r, sort_keys=True).encode()).hexdigest()[:24]
        if st.get("hash") == h and not st.get("errors") and st.get("pid") and P.get(st["pid"]):
            return st
        errors, paths = {}, {}
        folder = BASE / r["name"]
        for x in r["repos"]:
            have = (st.get("paths") or {}).get(x["label"])
            if have and Path(have).is_dir() and remote_of(have) == x["repo"]:
                paths[x["label"]] = have
                continue
            mine = _local_clone(x["repo"])
            owner_pid = P.project_for_path(mine) if mine else None
            same = next((q["id"] for q in P.all_projects() if q["name"].lower() == r["name"].lower()), None)
            if mine and mine not in paths.values() and owner_pid in (None, st.get("pid"), same):
                paths[x["label"]] = mine
                continue
            path, why = clone(x, folder / x["label"])
            if path:
                paths[x["label"]] = path
            else:
                errors[x["label"]] = why
        pid = self._project(st.get("pid"), r, author, paths)
        mem = _apply_memories(pid, author, r.get("memories") or []) if pid else 0
        st = {"from": author, "name": r["name"], "pid": pid, "paths": paths, "errors": errors, "at": time.time(),
              "hash": h if not errors else "", "memories": len(r.get("memories") or [])}
        follow = self.tt.load("follow.json", {})
        follow[key] = st
        self.tt.save("follow.json", follow)
        if pid:
            cfg = self.tt.config()
            if r["name"].lower() not in [p.lower() for p in cfg.get("projects", [])]:
                cfg.setdefault("projects", []).append(r["name"])
                self.tt.save("config.json", cfg)
        print(f"team projects: {r['name']} from {author}: {len(paths)} of {len(r['repos'])} repos ready"
              + (f", {mem} memories updated" if mem else "") + (f"; problems: {errors}" if errors else ""), flush=True)
        return st

    def _project(self, pid: str | None, r: dict, author: str, paths: dict) -> str | None:
        actor = f"team:{author}"
        ins = team_rule(author, self.me) + (("\n\n" + r["instructions"].strip()) if r.get("instructions", "").strip() else "")
        p = P.get(pid) if pid else None
        if p is None:
            p = next((x for x in P.all_projects() if x["name"].lower() == r["name"].lower() and x.get("status") == "active"), None)
        prim = next((paths[x["label"]] for x in r["repos"] if x.get("primary") and x["label"] in paths), None)
        try:
            if p is None:
                if not paths:
                    return None
                p = P.create(r["name"], description=r.get("description") or "", folders=list(paths.values()),
                             primary_path=prim, instructions=ins, actor=actor)
            else:
                have = {f["path"] for f in p.get("folders", [])}
                for path in paths.values():
                    if path not in have:
                        try:
                            p = P.add_folder(p["id"], path, actor=actor, primary_too=(path == prim and not p.get("folders")))
                        except store.MemoryError_ as e:
                            print(f"team projects: couldn't add {path} ({str(e)[:120]})", flush=True)
                cur = P.get(p["id"])
                fields = {}
                if (cur.get("instructions") or "") != ins:
                    fields["instructions"] = ins
                if r.get("description") and cur.get("description") != r["description"]:
                    fields["description"] = r["description"]
                if r.get("colours") and cur.get("colours") != r["colours"]:
                    fields["colours"] = r["colours"]
                if fields:
                    P.update(p["id"], expected_hash=None, actor=actor, **fields)
        except store.MemoryError_ as e:
            print(f"team projects: couldn't set up {r['name']} ({str(e)[:160]})", flush=True)
            return p["id"] if p else None
        self._webui_project(P.get(p["id"]) or p, actor)
        return p["id"]

    def _webui_project(self, p: dict, actor: str):
        """The WebUI's own project (sidebar group, chats that start in it), bound to the project's workspace."""
        if self.webui is None:
            return
        ws = str(P.workspace_path(p["id"]))
        try:
            wid = p.get("webui_project_id")
            if wid:
                code, all_ = self.webui.call("/api/projects")
                if code == 200 and not any(x.get("project_id") == wid for x in all_.get("projects") or []):
                    wid = None
            if not wid:
                code, made = self.webui.post("/api/projects/create", {"name": p["name"]})
                wid = ((made or {}).get("project") or {}).get("project_id") if code == 200 else None
                if not wid:
                    return
                P.update(p["id"], expected_hash=None, actor=actor, webui_project_id=wid)
            self.webui.post("/api/projects/bind", {"project_id": wid, "workspaces": [ws], "default_workspace": ws})
        except Exception as e:  # noqa: BLE001 (the project works without it; tried again next time)
            print(f"team projects: WebUI project for {p['name']} not made yet ({type(e).__name__})", flush=True)

    def sync_all(self) -> dict:
        """Fast-forward every received project's clones that are clean (never touches local work)."""
        out = {}
        for key, st in self.tt.load("follow.json", {}).items():
            for label, path in (st.get("paths") or {}).items():
                if Path(path).is_dir():
                    out[f"{key}/{label}"] = freshen(path)
        return out

    def freshen_project(self, name: str):
        """Right before a chat from the team site runs in a received project: bring its clones up to date."""
        for st in self.tt.load("follow.json", {}).values():
            if str(st.get("name", "")).lower() == (name or "").lower():
                for path in (st.get("paths") or {}).values():
                    if Path(path).is_dir():
                        freshen(path)

    async def run(self, mirror):
        """Publish this Mac's own shared projects; set up and sync the ones teammates publish."""
        last_pub = last_sync = 0.0
        while True:
            try:
                await asyncio.wait_for(self.wake.wait(), 30)
            except TimeoutError:
                pass
            self.wake.clear()
            await asyncio.sleep(2)  # let the index catch-up settle: only the newest recipe of each is applied
            try:
                for key, (_, author, r) in list(self.latest.items()):
                    await asyncio.to_thread(self.apply, key, author, r)
                    self.latest.pop(key, None)
                now = time.time()
                if now - last_pub > PUBLISH_EVERY:
                    last_pub = now
                    for r in await asyncio.to_thread(due_recipes, self.tt):
                        mirror.outbox.put_nowait(("index", "status", {k: v for k, v in r.items() if k != "hash"},
                                                  (lambda r=r: mark_published(self.tt, r))))
                if now - last_sync > SYNC_EVERY:
                    last_sync = now
                    await asyncio.to_thread(self.sync_all)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 (stay up)
                print(f"team projects: {type(e).__name__}: {str(e)[:200]}", flush=True)

