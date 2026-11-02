"""Migration: mirror existing knowledge into the store, idempotently.

- Claude Code auto-memory (~/.claude/projects/<cwd>/memory/*.md): Claude keeps writing these and
  loads them natively for that working directory, so they are imported as MIRRORS:
  `mirror_of: <original path>`, trust agent (Claude wrote them, often after reading files/web), not
  injected into worker prompts (Claude already has them), but searchable fleet-wide and analysed by
  the Dreamer (duplicates, staleness, cross-project lessons). Re-running updates a mirror only when
  the original changed (keyed by path + content hash). The original always wins.
- Anything the poisoning scan blocks is not imported; it becomes a pending proposal for review.
- ~/.hermes/memories/USER.md (Hermes built-in memory) is mirrored into global scope the same way.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from . import doc as D
from . import proposals, security, store, trace as T

CLAUDE_PROJECTS = T.CLAUDE_PROJECTS
_TYPE = {"user": "preference", "feedback": "preference", "project": "fact", "reference": "reference"}


def _project_for_dir(dirname: str) -> str | None:
    # "-Users-you-my-app" → ~/my-app (best effort: dashes may be real dashes)
    home = str(Path.home())
    guess = "/" + dirname.strip("-").replace("-", "/")
    if Path(guess).exists():
        return T.project_name(guess)
    parts = dirname.strip("-").split("-")
    for i in range(len(parts), 2, -1):  # longest existing prefix, rest re-joined with dashes
        head = "/" + "/".join(parts[:i - 1])
        cand = head + "/" + "-".join(parts[i - 1:])
        if Path(cand).exists():
            return T.project_name(cand)
    # folder gone: name it after everything below the home folder ("-Users-me-security-analysis" → security-analysis)
    home_parts = home.strip("/").split("/")
    rest = parts[len(home_parts):] if parts[:len(home_parts)] == home_parts else parts[-1:]
    return re.sub(r"[^a-z0-9._-]", "-", "-".join(rest).lower()) or None


def _sources() -> list[tuple[Path, str | None]]:
    out = []
    for f in sorted(CLAUDE_PROJECTS.glob("*/memory/*.md")):
        if f.name == "MEMORY.md":
            continue
        out.append((f, _project_for_dir(f.parent.parent.name)))
    u = Path.home() / ".hermes" / "memories" / "USER.md"
    if u.exists():
        out.append((u, None))
    return out


def run(dry_run: bool = False) -> dict:
    store.init()
    existing = {m.meta.get("mirror_of"): m for m in store.all_memories(include_inactive=True) if m.meta.get("mirror_of")}
    res = {"created": 0, "updated": 0, "unchanged": 0, "quarantined": 0, "errors": []}
    for src, project in _sources():
        text = src.read_text(errors="replace", encoding="utf-8")
        meta, body = D.parse(text)
        if not body.strip():
            continue
        h = D.sha(text)
        cur = existing.get(str(src))
        if cur and cur.meta.get("tags") and f"src-{h[:12]}" in cur.meta["tags"]:
            res["unchanged"] += 1
            continue
        m = re.search(r"(?m)^\s+type:\s*(\w+)", text.split("\n---", 1)[0])  # Claude nests it under metadata:
        mtype = (m.group(1) if m else None) or meta.get("type") or ("preference" if src.name == "USER.md" else "fact")
        external = bool(re.search(r"https?://|`[^`]*(curl|wget|\|\s*sh)", body))
        title = str(meta.get("name") or meta.get("description") or src.stem).replace("-", " ")[:80]
        scope = "project" if project else "global"
        new = {"id": (cur.id if cur else store.id_for(scope, f"mirror-{src.stem}", project)),
               "scope": scope, "project": project, "title": title,
               "description": str(meta.get("description") or title)[:200],
               "type": _TYPE.get(str(mtype), "fact") if not external else "reference",
               "status": "active", "trust": "external" if external else "agent",
               "created_by": "migration", "mirror_of": str(src),
               "tags": ["mirror", "claude-auto-memory" if src.name != "USER.md" else "hermes-user-md", f"src-{h[:12]}"]}
        findings = security.scan_fields(new, body)
        if security.blocked(findings):
            res["quarantined"] += 1
            if not dry_run:
                try:
                    proposals.create(type="create", target=new["id"], reason=f"migration: {src} was flagged by the scan",
                                     evidence=[{"file": str(src), "findings": findings}], source_sessions=[],
                                     proposed_change={"meta": {k: v for k, v in new.items() if k != "id"}, "body": body[:3900]},
                                     created_by="migration", trust="external", signature=f"migrate:{src}")
                except proposals.ProposalError as e:
                    res["errors"].append(f"{src}: {e}")
            continue
        if dry_run:
            res["updated" if cur else "created"] += 1
            continue
        try:
            store.write(new, body[:3900], actor="user", expected_hash=cur.hash if cur else None,
                        reason=f"mirror of {src}")
            res["updated" if cur else "created"] += 1
        except store.MemoryError_ as e:
            res["errors"].append(f"{src}: {e}")
    return res
