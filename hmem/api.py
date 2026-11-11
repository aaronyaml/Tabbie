"""Projects API for the WebUI (served by the cc-agents sidecar, behind the WebUI login + sidecar token).

`handle(method, path, query, body) -> (status, payload)` is plain Python so it can be tested without HTTP.
Every write goes through hmem.projects / hmem.proposals (locks, OCC, git) acting as the user: the only
way to reach this is the logged-in WebUI. The filesystem endpoints never return file contents except
the fixed allowlist of repo context files, and never list hidden or personal folders.
"""

from __future__ import annotations

import itertools
import os
import re
from pathlib import Path

from . import doc as D
from . import dreamer, projects, proposals, repo_suggest, retrieve, security, store, trace as T
from .paths import is_link

MAX_BODY = 64_000
_PID = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}-[0-9a-f]{4}$")
_PROP = re.compile(r"^p-\d{8}-[0-9a-f]{6}$")
_NO_BROWSE = {"Library", "Desktop", "Documents", "Downloads", "Movies", "Music", "Pictures", "Public", "Applications"}


class BadRequest(Exception):
    pass


def _need(d: dict, k: str, rx: re.Pattern | None = None) -> str:
    v = d.get(k)
    if not isinstance(v, str) or not v or (rx and not rx.match(v)):
        raise BadRequest(f"bad or missing {k}")
    return v


def _project(d: dict) -> dict:
    p = projects.get(_need(d, "id", _PID))
    if not p:
        raise BadRequest("no such project")
    return p


def _public(p: dict, h: str | None = None) -> dict:
    out = {k: p.get(k) for k in ("id", "name", "slug", "description", "status", "created_at", "updated_at", "version",
                                 "primary_folder", "instructions", "webui_project_id", "dreamer", "memory_keys")}
    out["colours"] = p.get("colours") or []
    out["folders"] = [{**f, "display": projects.tilde(f["path"])} for f in p.get("folders", [])]
    out["workspace"] = str(projects.workspace_path(p["id"]))
    if h:
        out["hash"] = h
    return out


def _with_hash(p: dict) -> dict:
    full, h = projects.load(p["id"])
    return _public(full, h)


def _traces(pid: str, limit: int = 60) -> list[dict]:
    ts = [t for t in T.load_all() if dreamer.owner(t) == pid]
    ts.sort(key=lambda t: t.get("started") or "", reverse=True)
    return ts[:limit]


def _session_row(t: dict) -> dict:
    conv = str(t.get("conversation") or "")
    return {"id": t["id"], "source": t.get("source"), "started": t.get("started"), "duration_s": t.get("duration_s"),
            "request": security.clean_title(t.get("user_request") or "", 160),
            "outcome": (t.get("outcome") or {}).get("label"), "folders": t.get("folders") or [],
            "models": (t.get("models") or [])[:3], "tokens": t.get("tokens") or t.get("output_tokens"),
            "errors": len(t.get("errors") or []), "files_modified": len(t.get("files_modified") or []),
            "webui_session": conv.split(":", 1)[1] if conv.startswith("hermes:") else None}


def _mem_row(m: store.Memory) -> dict:
    return {"id": m.id, "title": m.meta.get("title"), "description": m.meta.get("description"),
            "type": m.meta.get("type"), "trust": m.meta.get("trust"), "status": m.meta.get("status"),
            "version": m.meta.get("version"), "updated_at": m.meta.get("updated_at"),
            "mirror": bool(m.meta.get("mirror_of")), "trusted": store.is_committed(m),
            "quarantined_until": m.meta.get("inject_after") if (m.meta.get("inject_after") or "") > D.now_iso() else None}


def _project_mems(pid: str, include_inactive: bool = False) -> list[store.Memory]:
    keys = set(projects.memory_keys(pid))
    return [m for m in store.all_memories(include_inactive=include_inactive)
            if m.id.startswith("projects/") and m.id.split("/")[1] in keys]


def _proposals(pid: str, status: str) -> list[dict]:
    rows = [x for x in proposals.list_(status) if projects.project_of_proposal(x) == pid]
    rows.sort(key=lambda x: x.get("decided_at") or x.get("created_at") or "", reverse=True)
    return rows


def _target_title(key: str) -> str | None:
    try:
        m = store.read(key)
    except Exception:
        return None
    return m.meta.get("title") if m else None


def _proposal_row(x: dict) -> dict:
    ch = x.get("proposed_change") or {}
    title = (ch.get("meta") or {}).get("title") or _target_title(x["target"]) or x["target"]
    return {"id": x["proposal_id"], "type": x["type"], "target": x["target"], "status": x.get("status"),
            "title": title[:100], "reason": x.get("reason", "")[:300],
            "sessions": len(x.get("source_sessions") or []), "confidence": x.get("confidence"), "risk": x.get("risk"),
            "trust": x.get("trust"), "created_by": x.get("created_by"), "created_at": x.get("created_at"),
            "decided_at": x.get("decided_at"), "flags": proposals.needs_confirmation(x),
            "blocked": security.blocked(x.get("scan") or [])}


def _finding_row(f: dict) -> dict:
    s = f.get("support") or {}
    return {"specialist": f["specialist"], "signature": f["signature"], "title": f["title"], "severity": f["severity"],
            "conversations": s.get("conversations"), "days": s.get("days"), "folders": s.get("folders") or [],
            "affected_sessions": f.get("affected_sessions"), "relevant_sessions": f.get("relevant_sessions"),
            "confidence": f.get("confidence"), "recommended_action": f.get("recommended_action"),
            "report_only": f.get("report_only"), "detail": f.get("detail"), "sessions": (f.get("sessions") or [])[:20],
            "evidence": security.scrub_obj(f.get("evidence") or [])[:8]}


# ------------------------------------------------------------------ filesystem (folder picker)

def _browse_root(raw: str | None) -> Path:
    base = projects.allowed_root()
    p = Path(os.path.expanduser(raw or str(base)))
    try:
        p = projects.true_case(p.resolve(strict=True))
    except (OSError, RuntimeError):
        raise BadRequest("that folder doesn't exist on this computer") from None
    if not p.is_dir() or not p.is_relative_to(base):
        raise BadRequest(f"only folders inside {projects.tilde(str(base))} can be browsed")
    rel = p.relative_to(base).parts
    if rel and (rel[0] in _NO_BROWSE or any(x.startswith(".") for x in rel)):
        raise BadRequest("that folder can't be browsed")
    hermes = store.root().parent.resolve()
    if any(p == hermes / own or p.is_relative_to(hermes / own) for own in ("memory", "state", "backups", "workspaces")):
        raise BadRequest("Hermes' own memory/state folders can't be browsed")
    return p


def fs_list(raw: str | None) -> dict:
    p = _browse_root(raw)
    base = projects.allowed_root()
    owners = {f["path"]: pj["name"] for pj in projects.all_projects() for f in pj.get("folders", [])}
    rows = []
    try:
        with os.scandir(p) as it:
            kids = sorted((Path(e.path) for e in itertools.islice(it, 2000) if not e.name.startswith(".")),
                          key=lambda c: c.name.lower())
    except PermissionError:
        raise BadRequest("macOS didn't allow reading that folder") from None
    for c in kids[:400]:
        try:
            if not c.is_dir():
                continue
            real = c.resolve()
        except OSError:
            continue
        if p == base and c.name in _NO_BROWSE:
            continue
        why = projects._forbidden(real) if real.is_relative_to(base) else "outside the home folder"
        rows.append({"name": c.name, "path": str(real), "display": projects.tilde(str(real)), "git": (c / ".git").exists(),
                     "attachable": why is None, "why": why, "project": owners.get(str(real)), "link": c.is_symlink()})
    up = str(p.parent) if p != base else None
    return {"path": str(p), "display": projects.tilde(str(p)), "up": up, "entries": rows[:300]}


def fs_check(raw: str | None) -> dict:
    try:
        real = projects.canonical(raw or "")
    except projects.ProjectError as e:
        return {"ok": False, "why": str(e)}
    owner = projects.project_for_path(str(real))
    return {"ok": True, "path": str(real), "display": projects.tilde(str(real)), "git": projects._git_meta(real),
            "context_files": projects.context_files(real),
            "project": (projects.get(owner) or {}).get("name") if owner else None}


def fs_repos(q: str | None = None) -> dict:
    """Unclaimed git repos to suggest in the picker / New project sheet: most recently active first, or
    ranked against q (the project name being typed). See hmem/repo_suggest.py."""
    return repo_suggest.suggest(str(q or ""))


def repo_file(p: dict, folder_id: str, rel: str) -> dict:
    f = next((x for x in p.get("folders", []) if x["id"] == folder_id), None)
    if not f or not projects.folder_ok(f["path"]):
        raise BadRequest("no such folder")
    ok = rel in projects.CONTEXT_FILES or any(
        rel.startswith(d + "/") and rel.count("/") == d.count("/") + 1 and rel.endswith(".md") for d in projects.CONTEXT_DIRS)
    if not ok:
        raise BadRequest("only CLAUDE.md, AGENTS.md and .hermes/ docs can be viewed here")
    root = Path(f["path"])
    target = root / rel
    # the allowlisted name itself, not a symlink to something else in (or out of) the repo
    if any(is_link(x) for x in [target, *target.parents] if x.is_relative_to(root) and x != root):
        raise BadRequest("that file is a link; open it on the computer")
    import stat as _stat
    try:
        fd = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
    except OSError:
        raise BadRequest("file not found") from None
    with os.fdopen(fd, "rb") as fh:
        st = os.fstat(fh.fileno())
        if not _stat.S_ISREG(st.st_mode):
            raise BadRequest("file not found")
        text = fh.read(64_000).decode(errors="replace")
    return {"folder": f["label"], "path": rel, "text": security.scrub(text), "truncated": st.st_size > 64_000}


# ------------------------------------------------------------------ handlers

def _get(path: str, q: dict) -> dict:
    if path == "/projects":
        rows = []
        for p in projects.all_projects(include_archived=q.get("archived") == "1"):
            last = dreamer.last_cycle(p["id"]) or {}
            rows.append({**_public(p), "summary": projects.summary(p), "last_dream": last.get("at")})
        rows.sort(key=lambda r: r.get("updated_at") or "", reverse=True)
        return {"projects": rows}
    if path == "/projects/get":
        p = _project(q)
        out = _with_hash(p)
        for f in out["folders"]:
            f["live"] = projects.git_live(f["path"]) if f.get("git") and Path(f["path"]).is_dir() else None
            f["missing"] = not Path(f["path"]).is_dir()
        ts = _traces(p["id"], limit=500)
        last = dreamer.last_cycle(p["id"]) or {}
        out["summary"] = {**projects.summary(p), "sessions": len(ts),
                          "recent_sessions": sum(1 for t in ts if (t.get("started") or "") >= _days_ago(30)),
                          "last_dream": last.get("at"), "last_dream_findings": len(last.get("findings") or [])}
        out["legacy_candidates"] = projects.legacy_candidates(p)
        return out
    if path == "/projects/sessions":
        p = _project(q)
        return {"sessions": [_session_row(t) for t in _traces(p["id"], limit=int(q.get("limit") or 60))]}
    if path == "/projects/session":
        p = _project(q)
        tid = _need(q, "trace", re.compile(r"^[hcw]-[\w-]{6,40}$"))
        t = next((t for t in _traces(p["id"], limit=10_000) if t["id"] == tid), None)
        if not t:
            raise BadRequest("no such session in this project")
        keep = ("id", "source", "started", "ended", "duration_s", "user_request", "final_result", "outcome", "folders",
                "models", "tokens", "output_tokens", "files_read", "files_modified", "commands", "tests", "errors",
                "retries", "taint", "feedback", "route", "executor")
        return {k: t.get(k) for k in keep}
    if path == "/projects/memory":
        p = _project(q)
        mems = _project_mems(p["id"], include_inactive=q.get("all") == "1")
        return {"memories": sorted((_mem_row(m) for m in mems), key=lambda r: (r["type"] or "", r["title"] or ""))}
    if path == "/projects/memory/read":
        p = _project(q)
        mid = _need(q, "mem")
        m = next((m for m in _project_mems(p["id"], True) if m.id == mid), None)
        if not m:
            raise BadRequest("not a memory of this project")
        return {**_mem_row(m), "body": m.body, "meta": {k: v for k, v in m.meta.items() if k not in ("body",)},
                "history": store.history(m.id)[:20]}
    if path == "/projects/dreamer":
        p = _project(q)
        cyc = dreamer.cycles(p["id"], limit=10)
        last = cyc[0] if cyc else {}
        pend, appr, rej = (_proposals(p["id"], s) for s in ("pending", "approved", "rejected"))
        fs = last.get("findings") or []
        return {"running": _dreaming(p["id"]),
                "last": {k: last.get(k) for k in ("cycle", "at", "trigger", "seconds", "traces_analysed")} if last else None,
                "runs": [{"cycle": c["cycle"], "at": c["at"], "trigger": c.get("trigger"), "sessions": c.get("traces_analysed"),
                          "findings": len(c.get("findings") or []), "proposals": len(c.get("proposals") or [])} for c in cyc],
                "counts": {"pending": len(pend), "approved": len(appr), "rejected": len(rej),
                           "patterns": len(fs),
                           "recurring_failures": sum(1 for f in fs if f["specialist"] == "failures"),
                           "skills_proposed": sum(1 for x in pend if x["type"] == "skill"),
                           "memory_conflicts": sum(1 for f in fs if f["specialist"] == "conflicts"),
                           "stale_memories": sum(1 for f in fs if f["signature"].startswith(("stale:", "unused:")))},
                "findings": [_finding_row(f) for f in sorted(fs, key=lambda f: -f["severity"] * max(1, (f.get("support") or {}).get("conversations") or 1))[:60]],
                "pending": [_proposal_row(x) for x in pend],
                "decided": [_proposal_row(x) for x in (appr + rej)[:40]]}
    if path == "/projects/dreamer/run":
        p = _project(q)
        c = next((c for c in dreamer.cycles(p["id"], limit=50) if c["cycle"] == _need(q, "cycle")), None)
        if not c:
            raise BadRequest("no such run")
        return {**{k: c.get(k) for k in ("cycle", "at", "trigger", "seconds", "traces_analysed", "specialists", "skipped")},
                "findings": [_finding_row(f) for f in c.get("findings") or []]}
    if path == "/projects/proposal":
        p = _project(q)
        got = proposals.load(_need(q, "pid", _PROP))
        if not got or projects.project_of_proposal(got[0]) != p["id"]:
            raise BadRequest("not a proposal of this project")
        x = got[0]
        return {**_proposal_row(x), "evidence": x.get("evidence") or [], "source_sessions": x.get("source_sessions") or [],
                "diff": proposals.diff(x), "change": x.get("proposed_change"), "scan": x.get("scan") or [],
                "taint": x.get("taint") or [], "note": x.get("note")}
    if path == "/projects/skills":
        p = _project(q)
        return {"skills": projects.skills(p["id"]),
                "proposed": [_proposal_row(x) for x in _proposals(p["id"], "pending") if x["type"] == "skill"]}
    if path == "/projects/architecture":
        p = _project(q)
        docs = [_mem_row(m) for m in _project_mems(p["id"]) if m.meta.get("type") in ("decision", "architecture", "procedure")]
        repo = [{"folder_id": f["id"], "folder": f["label"], "files": projects.context_files(Path(f["path"]))}
                for f in p.get("folders", []) if Path(f["path"]).is_dir()]
        return {"memories": docs, "repo": repo}
    if path == "/projects/file":
        p = _project(q)
        return repo_file(p, _need(q, "folder"), _need(q, "path"))
    if path == "/projects/search":
        p = _project(q)
        hits = retrieve.search(str(q.get("q") or "")[:200], project=p["id"], k=12, actor="webui")
        return {"hits": [{"score": round(s, 2), **_mem_row(m)} for s, m in hits]}
    if path == "/fs/list":
        return fs_list(q.get("path"))
    if path == "/fs/check":
        return fs_check(q.get("path"))
    if path == "/fs/repos":
        return fs_repos(q.get("q"))
    raise LookupError(path)


def _post(path: str, b: dict) -> dict:
    if path == "/projects/create":
        folders = b.get("folders") or []
        if not isinstance(folders, list) or not all(isinstance(f, str) for f in folders):
            raise BadRequest("folders must be a list of paths")
        p = projects.create(str(b.get("name") or ""), description=str(b.get("description") or ""), folders=folders,
                            primary_path=b.get("primary") or None, instructions=str(b.get("instructions") or ""))
        claimed = projects.claim_history(p["id"])  # sessions that already ran in these folders
        return {**_with_hash(p), "claimed_sessions": claimed}
    p = _project(b)
    h = b.get("hash") if isinstance(b.get("hash"), str) else None
    if path == "/projects/update":
        fields = {k: b[k] for k in ("name", "description", "instructions", "webui_project_id", "dreamer", "memory_keys", "colours")
                  if k in b}
        return _with_hash(projects.update(p["id"], expected_hash=h, **fields))
    if path == "/projects/folder/add":
        chk = fs_check(_need(b, "path"))
        if not chk["ok"]:
            raise BadRequest(chk["why"])
        if b.get("confirm_path") != chk["path"]:  # the user saw (and confirmed) the real path being attached
            return {"confirm": chk}
        out = _with_hash(projects.add_folder(p["id"], chk["path"], expected_hash=h, primary_too=bool(b.get("primary"))))
        out["claimed_sessions"] = projects.claim_history(p["id"])
        return out
    if path == "/projects/folder/remove":
        return _with_hash(projects.remove_folder(p["id"], _need(b, "folder_id"), expected_hash=h))
    if path == "/projects/folder/primary":
        return _with_hash(projects.set_primary(p["id"], _need(b, "folder_id"), expected_hash=h))
    if path == "/projects/folder/refresh":
        return _with_hash(projects.refresh_folders(p["id"]))
    if path == "/projects/status":
        return _with_hash(projects.set_status(p["id"], _need(b, "status"), expected_hash=h))
    if path == "/projects/delete":
        if b.get("confirm_name") != p["name"]:
            raise BadRequest("type the project name to confirm")
        return projects.delete(p["id"])
    if path == "/projects/dream":
        if p.get("status") != "active":
            raise BadRequest("unarchive the project to run the Dreamer")
        if _dreaming(p["id"]):
            return {"started": False, "why": "already dreaming"}
        dreamer.spawn("manual", p["id"])
        return {"started": True}
    if path == "/projects/proposal/approve":
        x = _own_proposal(p, b)
        out = proposals.approve(x["proposal_id"], actor="user", confirm_flagged=b.get("confirm") is True)
        return {"ok": True, "proposal": _proposal_row(out)}
    if path == "/projects/proposal/reject":
        x = _own_proposal(p, b)
        out = proposals.reject(x["proposal_id"], actor="user", reason=str(b.get("reason") or "")[:300])
        return {"ok": True, "proposal": _proposal_row(out)}
    if path == "/projects/memory/delete":
        mid = _need(b, "mem")
        m = next((m for m in _project_mems(p["id"]) if m.id == mid), None)
        if not m:
            raise BadRequest("not a memory of this project")
        # archive = out of every prompt and list, file kept in archive/ and in git (recoverable)
        store.set_status(mid, "archived", actor="user", reason=f"deleted from project {p['id']} in the WebUI")
        return {"ok": True, "deleted": mid}
    if path == "/projects/claim":
        return {"claimed_sessions": projects.claim_history(p["id"])}
    raise LookupError(path)


def _own_proposal(p: dict, b: dict) -> dict:
    got = proposals.load(_need(b, "pid", _PROP))
    if not got or projects.project_of_proposal(got[0]) != p["id"]:
        raise BadRequest("not a proposal of this project")
    return got[0]


def _dreaming(pid: str) -> bool:
    from . import flockcompat as fcntl
    f = store.root() / ".locks" / f"dream-{pid}.lock"
    if not f.exists():
        return False
    with open(f, "w", encoding="utf-8") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fh, fcntl.LOCK_UN)
            return False
        except BlockingIOError:
            return True


def _days_ago(n: int) -> str:
    import datetime as dt
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=n)).isoformat()[:19]


def handle(method: str, path: str, query: dict | None = None, body: dict | None = None) -> tuple[int, dict]:
    try:
        store.init()
        if method == "GET":
            return 200, _get(path, query or {})
        if method == "POST":
            if not isinstance(body, dict):
                raise BadRequest("body must be a JSON object")
            return 200, _post(path, body)
        return 405, {"error": "method not allowed"}
    except LookupError:
        return 404, {"error": "not found"}
    except store.ConflictError as e:
        return 409, {"error": str(e), "conflict": True}
    except (BadRequest, proposals.ProposalError, store.MemoryError_) as e:
        return 400, {"error": str(e)}
