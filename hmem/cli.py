"""hmem command line. `~/hermes/bin/hmem <command> -h` for options.

  search "<words>" [--project P]     find memories (scope-isolated)
  read <id>                          show one memory (quoted, with provenance)
  index [--project P]                titles by scope
  add --scope S [--project P] --title T --body B   (user: direct write; others: becomes a proposal)
  propose create|modify|deprecate|merge|skill …    file a proposal
  proposals [pending|approved|rejected]            list
  show <proposal-id>                 review view (plain text, invisible chars shown)
  approve <proposal-id> [--confirm]  apply (user only)
  reject <proposal-id> [--reason R]
  history <id> / rollback <id> <sha> / deprecate <id> [--superseded-by X]
  trace [--since-days N]             build/refresh session traces (incremental)
  label <trace-id> good|bad          human outcome label (beats heuristics)
  dream [--dry-run] [--llm] [--trigger T]          run a Dreamer cycle
  migrate [--dry-run]                mirror Claude auto-memory + Hermes USER.md
  stats                              observability JSON
  doctor [--accept]                  files changed outside the store, git health
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import doc as D
from . import dreamer, migrate, projects, proposals, retrieve, stats, store, trace


def _p(x) -> None:
    print(json.dumps(x, indent=1, ensure_ascii=False, default=str) if not isinstance(x, str) else x)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hmem", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search"); s.add_argument("query"); s.add_argument("--project"); s.add_argument("--agent")
    s.add_argument("-k", type=int, default=8)
    s = sub.add_parser("read"); s.add_argument("id")
    s = sub.add_parser("index"); s.add_argument("--project")
    s = sub.add_parser("add")
    for a in ("--scope", "--title", "--body"):
        s.add_argument(a, required=True)
    s.add_argument("--project"); s.add_argument("--agent"); s.add_argument("--description", default="")
    s.add_argument("--type", default="fact"); s.add_argument("--tags", default=""); s.add_argument("--actor", default="user")
    s.add_argument("--refs", default="")
    s = sub.add_parser("propose"); s.add_argument("type", choices=proposals.TYPES); s.add_argument("--target", default="")
    s.add_argument("--reason", required=True); s.add_argument("--body", default=""); s.add_argument("--title", default="")
    s.add_argument("--scope", default="project"); s.add_argument("--project"); s.add_argument("--status", default="deprecated")
    s.add_argument("--superseded-by"); s.add_argument("--supersede", default=""); s.add_argument("--skill-file")
    s.add_argument("--actor", default="agent:cli"); s.add_argument("--evidence", default="")
    s.add_argument("--sessions", default="")
    s = sub.add_parser("proposals"); s.add_argument("status", nargs="?", default="pending")
    s = sub.add_parser("show"); s.add_argument("id")
    s = sub.add_parser("approve"); s.add_argument("id"); s.add_argument("--confirm", action="store_true")
    s = sub.add_parser("reject"); s.add_argument("id"); s.add_argument("--reason", default="")
    s = sub.add_parser("history"); s.add_argument("id")
    s = sub.add_parser("rollback"); s.add_argument("id"); s.add_argument("sha")
    s = sub.add_parser("deprecate"); s.add_argument("id"); s.add_argument("--superseded-by"); s.add_argument("--reason", default="")
    s = sub.add_parser("trace"); s.add_argument("--since-days", type=int)
    s = sub.add_parser("label"); s.add_argument("trace"); s.add_argument("value", choices=("good", "bad"))
    s = sub.add_parser("dream"); s.add_argument("--dry-run", action="store_true"); s.add_argument("--llm", action="store_true")
    s.add_argument("--trigger", default="manual"); s.add_argument("--quiet", action="store_true")
    s.add_argument("--only", default=""); s.add_argument("--project")
    s = sub.add_parser("project"); s.add_argument("action", choices=("list", "show", "create", "add-folder",
                                                                     "remove-folder", "primary", "archive", "unarchive",
                                                                     "claim-history", "context"))
    s.add_argument("id", nargs="?"); s.add_argument("--name"); s.add_argument("--description", default="")
    s.add_argument("--folder", action="append", default=[]); s.add_argument("--primary")
    s = sub.add_parser("migrate"); s.add_argument("--dry-run", action="store_true")
    sub.add_parser("stats")
    s = sub.add_parser("doctor"); s.add_argument("--accept", action="store_true")
    sub.add_parser("init")
    a = ap.parse_args(argv)
    try:
        return _run(a)
    except (store.MemoryError_, proposals.ProposalError, ValueError) as e:
        print(f"hmem: {e}", file=sys.stderr)
        return 2


_USER_ONLY = {"approve", "reject", "deprecate", "doctor", "migrate"}
_NEEDS_TTY = {"approve", "reject", "deprecate"}
_USER_ONLY_PROJECT = {"create", "add-folder", "remove-folder", "primary", "archive", "unarchive", "claim-history"}


def _run(a) -> int:
    c = a.cmd
    if os.environ.get("HERMES_WORKER"):
        # a worker (possibly steered by a repo file or web page) is not the user. It may propose; the user decides.
        if c in _USER_ONLY or (c == "project" and a.action in _USER_ONLY_PROJECT) or (c == "doctor" and a.accept):
            raise ValueError(f"`hmem {c}` is for the user (WebUI or their own terminal), not a worker; use `hmem propose`")
        if c == "add" and a.actor == "user":
            a.actor = "agent"
    if c in _NEEDS_TTY and not sys.stdin.isatty() and os.environ.get("HMEM_ALLOW_NO_TTY") != "1":
        # HERMES_WORKER can be unset by whoever runs the command; a worker's shell has no terminal, the user's does
        raise ValueError(f"`hmem {c}` needs your own terminal (or the WebUI); workers can only `hmem propose`")
    if c == "init":
        _p(str(store.init()))
    elif c == "search":
        hits = retrieve.search(a.query, project=a.project, agent=a.agent, k=a.k, actor="cli")
        for sc, m in hits:
            print(f"{sc:5.2f}  {m.id}  [{m.meta.get('trust')}]  {m.meta.get('title')}")
        if not hits:
            print("no matches (scope: global" + (f" + projects/{a.project}" if a.project else "") + ")")
    elif c == "read":
        out = retrieve.read_for_agent(a.id, actor="cli")
        print(out or "not found (or changed outside the store: run `hmem doctor`)")
    elif c == "index":
        for m in store.trusted_memories():
            sc, owner = store.scope_of(m.id)
            if sc == "global" or owner == a.project or not a.project:
                print(f"{m.id}  — {m.meta.get('title')}")
    elif c == "add":
        meta = {"scope": a.scope, "project": a.project, "agent": a.agent, "title": a.title,
                "description": a.description or a.title, "type": a.type,
                "tags": [t for t in a.tags.split(",") if t], "refs": [r for r in a.refs.split(",") if r]}
        if store.can_write(a.actor, a.scope, a.project, a.agent):
            _p({"written": store.write(meta, a.body, actor=a.actor, create_only=True).id})
        else:
            meta["id"] = store.id_for(a.scope, a.title, a.project, a.agent)
            p = proposals.create(type="create", target=meta["id"], reason=f"{a.actor} added a memory",
                                 evidence=[], source_sessions=[], proposed_change={"meta": meta, "body": a.body},
                                 created_by=a.actor)
            _p({"proposed": p["proposal_id"], "why": f"{a.actor} cannot write {a.scope} memory directly"})
    elif c == "propose":
        ch: dict = {}
        target = a.target
        if a.type in ("create", "modify", "merge"):
            cur = store.read(target) if a.type == "modify" else None
            meta = dict(cur.meta) if cur else {"scope": a.scope, "project": a.project, "title": a.title,
                                               "description": a.title, "type": "fact"}
            if a.title:
                meta["title"] = a.title
            for k in ("version", "created_at", "updated_at", "inject_after"):
                meta.pop(k, None)
            target = target or store.id_for(meta["scope"], meta["title"], meta.get("project"))
            ch = {"meta": meta, "body": a.body}
            if a.type == "merge":
                ch["supersede"] = [x for x in a.supersede.split(",") if x]
        elif a.type == "deprecate":
            ch = {"status": a.status, "superseded_by": a.superseded_by}
        elif a.type == "skill":
            ch = {"skill_md": open(a.skill_file).read()}
        key = store.scope_of(target)[1] if target.startswith("projects/") else None
        p = proposals.create(type=a.type, target=target, reason=a.reason, proposed_change=ch, created_by=a.actor,
                             evidence=[e for e in a.evidence.split("|") if e],
                             source_sessions=[x for x in a.sessions.split(",") if x],
                             project=projects.owner_of_key(key or a.project or ""))
        _p({"proposal": p["proposal_id"], "flags": proposals.needs_confirmation(p)})
    elif c == "proposals":
        for p in proposals.list_(a.status):
            flags = " !" if proposals.needs_confirmation(p) else ""
            print(f"{p['proposal_id']}  [{p['type']}] {p['target']}  ({len(p.get('source_sessions') or [])} sessions, "
                  f"trust {p.get('trust')}){flags}  {p.get('status') if p.get('status') != a.status else ''}")
    elif c == "show":
        got = proposals.load(a.id)
        print(proposals.render(got[0]) if got else "not found")
    elif c == "approve":
        p = proposals.approve(a.id, actor="user", confirm_flagged=a.confirm)
        _p({"approved": p["proposal_id"], "target": p["target"]})
    elif c == "reject":
        _p({"rejected": proposals.reject(a.id, actor="user", reason=a.reason)["proposal_id"]})
    elif c == "history":
        _p(store.history(a.id))
    elif c == "rollback":
        _p({"restored": store.rollback(a.id, a.sha).id})
    elif c == "deprecate":
        st = "superseded" if a.superseded_by else "deprecated"
        _p({"status": store.set_status(a.id, st, actor="user", superseded_by=a.superseded_by, reason=a.reason).meta["status"]})
    elif c == "trace":
        _p(trace.collect(since_days=a.since_days))
    elif c == "label":
        trace.label(a.trace, a.value)
        _p({"labelled": a.trace, "as": a.value})
    elif c == "dream":
        only = [x for x in a.only.split(",") if x] or None
        r = dreamer.run_cycle(trigger=a.trigger, dry_run=a.dry_run, llm=a.llm or None, only=only, project=a.project)
        if a.trigger == "nightly" and not a.project:  # then each Project with new sessions since its last dream
            r = {"global": r, "projects": {}}
            for pj in projects.all_projects():
                last = (dreamer.last_cycle(pj["id"]) or {}).get("at", "")
                if any(dreamer.owner(t) == pj["id"] for t in trace.load_all(since=last[:19] or None)):
                    r["projects"][pj["id"]] = dreamer.run_cycle(trigger="nightly", collect=False, project=pj["id"])
        if not a.quiet:
            _p(r)
    elif c == "project":
        _project(a)
    elif c == "migrate":
        _p(migrate.run(dry_run=a.dry_run))
    elif c == "stats":
        _p(stats.summary())
    elif c == "doctor":
        _p(store.doctor(accept=a.accept))
    return 0


def _project(a) -> None:
    act = a.action
    if act == "list":
        for pj in projects.all_projects(include_archived=True):
            print(f"{pj['id']}  {pj['name']}  ({len(pj['folders'])} folders, {pj['status']})")
        return
    if act == "create":
        _p(projects.create(a.name or a.id or "", description=a.description, folders=a.folder, primary_path=a.primary))
        return
    pj = projects.find(a.id or "") or projects.get(a.id or "")
    if not pj:
        raise ValueError(f"no project {a.id!r} (try `hmem project list`)")
    pid = pj["id"]
    if act == "show":
        _p({**pj, "summary": projects.summary(pj)})
    elif act == "context":
        print(projects.context_block(pj))
    elif act == "add-folder":
        for f in a.folder:
            pj = projects.add_folder(pid, f)
        _p({"folders": [x["path"] for x in pj["folders"]]})
    elif act == "remove-folder":
        for f in a.folder:
            fid = next((x["id"] for x in pj["folders"] if x["path"] == str(projects.canonical(f)) or x["id"] == f), f)
            pj = projects.remove_folder(pid, fid)
        _p({"folders": [x["path"] for x in pj["folders"]]})
    elif act == "primary":
        real = str(projects.canonical(a.primary or (a.folder or [""])[0]))
        fid = next((x["id"] for x in pj["folders"] if x["path"] == real), None)
        if not fid:
            raise ValueError("that folder is not in the project")
        _p({"primary": projects.set_primary(pid, fid)["primary_folder"]})
    elif act in ("archive", "unarchive"):
        _p({"status": projects.set_status(pid, "archived" if act == "archive" else "active")["status"]})
    elif act == "claim-history":
        _p({"claimed": projects.claim_history(pid)})


if __name__ == "__main__":
    sys.exit(main())
