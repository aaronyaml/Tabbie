"""The memory store: Markdown files under ~/hermes/memory, versioned in their own git repo.

Guarantees
- Atomic writes (tmp + fsync + rename): a reader never sees a half-written memory.
- Optimistic concurrency: write(expected_hash=...) fails with ConflictError if the file changed
  since the caller read it. The check-and-write runs under a short per-document flock.
- Permissions: shared scopes (global/project/agent) only accept direct writes from the actors
  allowed in can_write(); everyone else must go through proposals (see proposals.py).
- Nothing is destroyed: deprecate/supersede/archive move or mark, every mutation is a git
  commit (author = actor, message carries provenance), so `git log/diff/revert` restore anything.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from . import doc as D
from . import flockcompat as fcntl
from . import security

DEFAULT_ROOT = Path.home() / "hermes" / "memory"
SHARED = ("global", "project", "agent")
CURATED_DIRS = ("global", "projects", "agents")


def root() -> Path:
    if os.environ.get("HMEM_ROOT"):
        return Path(os.environ["HMEM_ROOT"]).expanduser()
    if os.environ.get("COMMAND_CENTRE_HOME"):  # a test/sandbox Hermes home gets its own memory too
        return Path(os.environ["COMMAND_CENTRE_HOME"]).expanduser() / "memory"
    return DEFAULT_ROOT


class MemoryError_(Exception):
    pass


class ConflictError(MemoryError_):
    def __init__(self, msg: str, current_hash: str | None):
        super().__init__(msg)
        self.current_hash = current_hash


class PermissionDenied(MemoryError_):
    pass


class ValidationError(MemoryError_):
    pass


@dataclass
class Memory:
    meta: dict
    body: str
    path: Path
    hash: str
    text_raw: str = ""

    @property
    def id(self) -> str:
        return self.meta.get("id", "")

    def text(self) -> str:
        return D.render(self.meta, self.body)


# ------------------------------------------------------------------ ids / paths

def id_for(scope: str, name: str, project: str | None = None, agent: str | None = None) -> str:
    s = D.slug(name)
    if scope == "global":
        return f"global/{s}"
    if scope == "project":
        return f"projects/{D.slug(project or '')}/{s}"
    if scope == "agent":
        return f"agents/{D.slug(agent or '')}/{s}"
    raise ValidationError(f"no curated id for scope {scope!r}")


def path_for(mem_id: str, base: Path | None = None) -> Path:
    base = (base or root()).resolve()
    if not mem_id or ".." in mem_id.split("/") or mem_id.startswith("/"):
        raise ValidationError(f"bad id {mem_id!r}")
    if mem_id.split("/")[0] not in CURATED_DIRS:
        raise ValidationError(f"id must start with one of {CURATED_DIRS}: {mem_id!r}")
    p = (base / f"{mem_id}.md").resolve()
    if not p.is_relative_to(base):
        raise ValidationError(f"id escapes the store: {mem_id!r}")
    return p


def scope_of(mem_id: str) -> tuple[str, str | None]:
    parts = mem_id.split("/")
    if parts[0] == "global":
        return "global", None
    if parts[0] == "projects" and len(parts) >= 3:
        return "project", parts[1]
    if parts[0] == "agents" and len(parts) >= 3:
        return "agent", parts[1]
    raise ValidationError(f"cannot tell scope of {mem_id!r}")


# ------------------------------------------------------------------ permissions

def can_write(actor: str, scope: str, project: str | None = None, agent: str | None = None) -> bool:
    """Who may write a shared scope directly. Everyone else proposes."""
    if scope == "session":
        return True
    if actor == "user" or actor.startswith("proposal:"):  # an approved proposal applies on the user's authority
        return True
    if scope == "project":
        return actor == f"manager:{project}"
    if scope == "agent":
        return actor == f"agent:{agent}"
    return False  # global: user only


# ------------------------------------------------------------------ io primitives

def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


@contextlib.contextmanager
def _lock(name: str, timeout: float = 30.0):
    d = root() / ".locks"
    d.mkdir(parents=True, exist_ok=True)
    f = open(d / f"{D.slug(name)}.lock", "w")
    deadline = time.time() + timeout
    while True:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.time() > deadline:
                f.close()
                raise MemoryError_(f"lock {name} busy")
            time.sleep(0.05)
    try:
        yield
    finally:
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()


def append_event(kind: str, **fields) -> None:
    """Append-only observability log (one line per event, single write => atomic for small lines)."""
    d = root() / ".stats"
    d.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"at": D.now_iso(), "kind": kind, **fields}, ensure_ascii=False, default=str)[:4000] + "\n"
    fd = os.open(d / "events.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, line.encode())
    finally:
        os.close(fd)


# ------------------------------------------------------------------ git

def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root()), *args], capture_output=True, text=True, check=check,
                          env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})


def init() -> Path:
    """Create the layout + git repo (idempotent)."""
    r = root()
    for d in ("global", "projects", "agents", "proposals/pending", "proposals/approved", "proposals/rejected",
              "dreams", "archive", "sessions", ".stats", ".locks"):
        (r / d).mkdir(parents=True, exist_ok=True)
    gi = r / ".gitignore"
    want = "INDEX.md\nsessions/\n.stats/\n.locks/\n.archive/\n*.tmp\n.*.md.*\n"
    if not gi.exists() or gi.read_text() != want:
        _atomic_write(gi, want)
    readme = Path(__file__).with_name("README.memory.md")
    if readme.exists() and (not (r / "README.md").exists() or (r / "README.md").read_text() != readme.read_text()):
        _atomic_write(r / "README.md", readme.read_text())
    if not (r / ".git").exists():
        with _lock("store"):  # two first-time callers at once would both `git init` and clash on its config lock
            if not (r / ".git").exists():
                _git("init", "-q", "-b", "main")
                commit(["."], "user", "init memory store")
    elif _git("status", "--porcelain", "--", "README.md", ".gitignore", check=False).stdout.strip():
        with _lock("store"):
            commit(["README.md", ".gitignore"], "user", "update memory map")
    return r


def commit(paths: list[str], actor: str, message: str) -> str | None:
    """Commit the given paths (relative to the store). Returns the commit sha or None (nothing to commit
    / git unavailable). Serialised by a repo lock so concurrent writers never fight over index.lock."""
    r = root()
    if not (r / ".git").exists():
        return None
    with _lock("git", timeout=60):
        with contextlib.suppress(OSError):
            stale = r / ".git" / "index.lock"
            if stale.exists() and time.time() - stale.stat().st_mtime > 120:
                stale.unlink()  # left by a crashed git; we hold the repo lock so nobody else is in git
        try:
            _git("add", "-A", "--", *paths)
            if not _git("diff", "--cached", "--quiet", check=False).returncode:
                return None
            name = actor.replace("<", "").replace(">", "")[:60]
            _git("-c", f"user.name={name}", "-c", "user.email=hmem@hermes.local", "commit", "-q", "-m", message[:2000])
            return _git("rev-parse", "HEAD").stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            append_event("git_failed", error=str(getattr(e, "stderr", e))[:300], paths=paths)
            return None


# ------------------------------------------------------------------ read

def read(mem_id: str) -> Memory | None:
    p = path_for(mem_id)
    if not p.exists():
        return None
    text = p.read_text()
    meta, body = D.parse(text)
    return Memory(meta, body, p, D.sha(text), text)


def all_memories(include_inactive: bool = False) -> list[Memory]:
    r = root()
    out = []
    for top in CURATED_DIRS:
        for p in sorted((r / top).rglob("*.md")):
            if any(part.startswith(".") for part in p.relative_to(r).parts):
                continue
            text = p.read_text()
            meta, body = D.parse(text)
            if not meta.get("id"):
                continue
            if include_inactive or meta.get("status", "active") == "active":
                out.append(Memory(meta, body, p, D.sha(text), text))
    return out


# ------------------------------------------------------------------ integrity
# Workers run with file tools, so a hijacked worker could edit memory/*.md directly and skip
# proposals/approval. Loaders that feed other agents (recall, worker prompt titles, INDEX) only
# trust files whose content equals what the store committed (git HEAD). Anything else is ignored
# and reported by doctor() until the user accepts or reverts it.

_HEAD_CACHE: dict = {"head": None, "blobs": {}}


def _blob_sha(text: str) -> str:
    import hashlib
    data = text.encode()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def committed_blobs() -> dict[str, str]:
    if not (root() / ".git").exists():
        return {}
    head = _git("rev-parse", "-q", "--verify", "HEAD", check=False).stdout.strip()
    if head != _HEAD_CACHE["head"]:
        out = _git("ls-tree", "-r", "HEAD", check=False).stdout if head else ""
        _HEAD_CACHE["blobs"] = {ln.split("\t", 1)[1]: ln.split()[2] for ln in out.splitlines() if "\t" in ln}
        _HEAD_CACHE["head"] = head
    return _HEAD_CACHE["blobs"]


def is_committed(m: "Memory", blobs: dict[str, str] | None = None) -> bool:
    """blobs: a committed_blobs() result to reuse (one HEAD lookup for a whole batch of memories)."""
    rel = str(m.path.resolve().relative_to(root().resolve()))
    return (committed_blobs() if blobs is None else blobs).get(rel) == _blob_sha(m.text_raw)


def trusted_memories() -> list["Memory"]:
    """Active memories whose on-disk content is exactly what the store committed."""
    out, bad = [], []
    blobs = committed_blobs()  # one `git rev-parse` per search, not one per memory
    for m in all_memories():
        (out if is_committed(m, blobs) else bad).append(m)
    if bad:
        append_event("integrity_mismatch", ids=[m.id for m in bad][:20])
    return out


def doctor(accept: bool = False) -> dict:
    """Report curated files changed outside the store (and stale git locks). accept=True commits them
    as the user's own edit (use after reviewing `git -C ~/hermes/memory diff`)."""
    r = root()
    st = _git("status", "--porcelain", "--", *CURATED_DIRS, "proposals", check=False).stdout.splitlines()
    report = {"uncommitted": [ln[3:] for ln in st], "index_lock": (r / ".git" / "index.lock").exists()}
    if accept and report["uncommitted"]:
        with _lock("store"):
            report["committed"] = commit(list(CURATED_DIRS) + ["proposals"], "user", "accept manual edits (hmem doctor)")
    return report


# ------------------------------------------------------------------ write

def write(meta: dict, body: str, *, actor: str, expected_hash: str | None = None, create_only: bool = False,
          reason: str = "", allow_blocked: bool = False) -> Memory:
    """Create or update one memory. expected_hash = the hash the caller read (None = must not exist
    unless create_only is False and the caller accepts last-writer... which we never allow: updating an
    existing memory REQUIRES expected_hash)."""
    meta = {k: v for k, v in meta.items() if v is not None}
    scope = meta.get("scope")
    if scope == "session":
        raise ValidationError("session notes live in the session trace dir, not the curated store")
    if not can_write(actor, scope, meta.get("project"), meta.get("agent")):
        raise PermissionDenied(f"{actor} cannot write {scope} memory directly; create a proposal instead")
    mem_id = meta.get("id") or id_for(scope, meta.get("title", ""), meta.get("project"), meta.get("agent"))
    meta["id"] = mem_id
    sc, owner = scope_of(mem_id)
    if sc != scope or (sc == "project" and owner != meta.get("project")) or (sc == "agent" and owner != meta.get("agent")):
        raise ValidationError(f"id {mem_id} does not match scope {scope}/{meta.get('project') or meta.get('agent')}")
    body = security.scrub(body.strip())
    meta.setdefault("status", "active")
    meta.setdefault("type", "fact")
    meta.setdefault("trust", "user" if actor == "user" else "agent")
    findings = security.scan_fields(meta, body)
    bad_refs = [r for r in meta.get("refs") or [] if not security.safe_ref(r)]
    if bad_refs:
        raise ValidationError(f"refs must be plain paths inside your home folder, not credentials: {bad_refs}")
    if security.blocked(findings) and not allow_blocked:
        append_event("write_blocked", id=mem_id, actor=actor, findings=findings)
        raise ValidationError("blocked by poisoning scan: " + "; ".join(f["rule"] for f in findings if f["severity"] == "block"))
    p = path_for(mem_id)
    with _lock("store"):
        cur = p.read_text() if p.exists() else None
        if cur is not None:
            if create_only:
                raise ConflictError(f"{mem_id} already exists", D.sha(cur))
            if expected_hash is None or D.sha(cur) != expected_hash:
                raise ConflictError(f"{mem_id} changed since it was read (reload and reconcile)", D.sha(cur))
            old, _ = D.parse(cur)
            meta.setdefault("created_at", old.get("created_at"))
            meta.setdefault("created_by", old.get("created_by"))
            meta["version"] = int(old.get("version") or 1) + 1
        else:
            if expected_hash not in (None, ""):
                raise ConflictError(f"{mem_id} no longer exists", None)
            meta.setdefault("created_at", D.now_iso())
            meta.setdefault("created_by", actor)
            meta["version"] = 1
        meta["updated_at"] = D.now_iso()
        errs = D.validate(meta, body)
        if errs:
            raise ValidationError("; ".join(errs))
        text = D.render(meta, body)
        _atomic_write(p, text)
        rel = str(p.relative_to(root().resolve()))
        commit([rel], actor, f"{'update' if cur else 'create'} {mem_id} v{meta['version']}\n\nactor: {actor}\n"
                             f"reason: {reason or '-'}\nsources: {', '.join(meta.get('source_sessions') or []) or '-'}")
    append_event("write", id=mem_id, actor=actor, version=meta["version"], created=cur is None)
    write_index()
    return Memory(meta, body, p, D.sha(text), text)


def set_status(mem_id: str, status: str, *, actor: str, expected_hash: str | None = None,
               superseded_by: str | None = None, reason: str = "") -> Memory:
    """deprecate / supersede / archive. Archive moves the file to archive/ (still in git)."""
    m = read(mem_id)
    if not m:
        raise MemoryError_(f"{mem_id} not found")
    if status not in D.STATUSES:
        raise ValidationError(f"bad status {status}")
    meta = dict(m.meta, status=status)
    if superseded_by:
        meta["superseded_by"] = superseded_by
    updated = write(meta, m.body, actor=actor, expected_hash=expected_hash or m.hash, reason=reason or status)
    if status == "archived":
        dest = root() / "archive" / f"{mem_id.replace('/', '__')}.md"
        with _lock("store"):
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(updated.path, dest)
            commit([str(updated.path.relative_to(root().resolve())), str(dest.relative_to(root()))], actor,
                   f"archive {mem_id}\n\nreason: {reason or '-'}")
        write_index()
    return updated


def history(mem_id: str, n: int = 20) -> list[dict]:
    rel = str(path_for(mem_id).relative_to(root().resolve()))
    r = _git("log", f"-n{n}", "--format=%H%x1f%an%x1f%aI%x1f%s", "--follow", "--", rel, check=False)
    return [dict(zip(("sha", "actor", "at", "subject"), ln.split("\x1f"))) for ln in r.stdout.splitlines() if ln]


def rollback(mem_id: str, to_sha: str, *, actor: str = "user") -> Memory:
    """Restore a memory to its content at commit to_sha (itself a new, auditable commit)."""
    if actor != "user":
        raise PermissionDenied("only the user rolls memory back")
    rel = str(path_for(mem_id).relative_to(root().resolve()))
    old = _git("show", f"{to_sha}:{rel}").stdout
    cur = read(mem_id)
    meta, body = D.parse(old)
    meta.pop("version", None)
    return write(meta, body, actor=actor, expected_hash=cur.hash if cur else None,
                 reason=f"rollback to {to_sha[:10]}")


# ------------------------------------------------------------------ index

def write_index() -> None:
    by: dict[str, list[Memory]] = {}
    for m in trusted_memories():
        sc, owner = scope_of(m.id)
        by.setdefault(sc if sc == "global" else f"{sc}: {owner}", []).append(m)
    lines = ["# Memory index (generated by hmem; edit memories, not this file)", "",
             "Active memories only. Read README.md for how to search and what each scope means.", ""]
    for key in sorted(by, key=lambda k: (k != "global", k)):
        lines.append(f"## {key}")
        for m in sorted(by[key], key=lambda x: x.id):
            lines.append(f"- [{m.meta.get('title', m.id)}]({m.id}.md) — {m.meta.get('description', '')}")
        lines.append("")
    with _lock("index"):
        _atomic_write(root() / "INDEX.md", "\n".join(lines))
