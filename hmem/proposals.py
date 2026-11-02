"""Memory proposals: the only way non-user actors change shared memory.

A proposal is one JSON file in memory/proposals/{pending,approved,rejected}/<id>.json:

  proposal_id, type (create|modify|deprecate|merge|skill), target (memory id or skill name),
  reason, evidence [{session, quote}], source_sessions, confidence, risk, trust, taint,
  signature (dedupe/suppression key), base_hash (target content hash when proposed),
  proposed_change {meta, body} | {status, superseded_by} | {skill_md}, created_by, created_at,
  scan (poisoning findings), status, decided_at, decided_by, applied_commit, note

Rules
- Only the user approves or rejects.
- Proposals whose target changed since they were made are marked stale, never force-applied.
- Approval re-runs validation; anything flagged or from external-trust evidence needs
  confirm_flagged=True (the user saw the warnings).
- Applied memories that the user did not write become visible to worker prompts 24 h later.
- Rejecting suppresses the same signature for 30 days; at most MAX_PENDING wait at once.
"""

from __future__ import annotations

import datetime as _dt
import json, re
import secrets
from pathlib import Path

from . import doc as D
from . import security, store

TYPES = ("create", "modify", "deprecate", "merge", "skill")
MAX_PENDING = 25          # waiting at once outside any Project
MAX_PENDING_PROJECT = 15  # per Project, so one noisy project can't starve the others
SUPPRESS_DAYS = 30
QUARANTINE_HOURS = 24


class ProposalError(Exception):
    pass


def _dir(status: str) -> Path:
    return store.root() / "proposals" / status


def _path(pid: str, status: str) -> Path:
    if not D.valid_slug(pid):
        raise ProposalError(f"bad proposal id {pid!r}")
    return _dir(status) / f"{pid}.json"


def load(pid: str) -> tuple[dict, str] | None:
    for st in ("pending", "approved", "rejected"):
        p = _path(pid, st)
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8")), st
    return None


def list_(status: str = "pending") -> list[dict]:
    d = _dir(status)
    if not d.exists():
        return []
    return sorted((json.loads(p.read_text(encoding="utf-8")) for p in d.glob("*.json")), key=lambda x: x.get("created_at", ""))


def _suppressed(signature: str, project: str | None = None) -> bool:
    """Rejections count only inside their own scope: rejecting in Acme says nothing about Globex."""
    if not signature:
        return False
    cutoff = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=SUPPRESS_DAYS)).isoformat()
    return any(p.get("signature") == signature and (p.get("project") or None) == (project or None)
               and (p.get("decided_at") or "") >= cutoff[:19] for p in list_("rejected"))


def _check_target(type: str, target: str, project: str | None) -> None:
    """A project's proposal may only touch that project's memory (its id or its included legacy keys)."""
    from . import projects
    if not project:
        if target.startswith("projects/") and projects.owner_of_key(target.split("/")[1]):
            raise ProposalError("that memory belongs to a project; propose it from that project")
        return
    if type == "skill":
        return
    keys = projects.memory_keys(project)
    if not (target.startswith("projects/") and target.split("/")[1] in keys):
        raise ProposalError(f"a {project} proposal can only change {project}'s own memory")


def _validate_change(p: dict) -> list[str]:
    errs = []
    ch = p.get("proposed_change") or {}
    if p["type"] in ("create", "modify", "merge"):
        meta, body = dict(ch.get("meta") or {}), ch.get("body") or ""
        meta.setdefault("id", p.get("target"))
        meta.setdefault("status", "active")
        meta.setdefault("created_by", p["created_by"])
        meta["trust"] = D.trust_min(p.get("trust") or "agent", meta.get("trust") or "agent")
        errs += D.validate(meta, body)
        if p["type"] == "merge" and not ch.get("supersede"):
            errs.append("merge needs supersede: [ids]")
    elif p["type"] == "deprecate":
        if ch.get("status") not in ("deprecated", "superseded", "archived"):
            errs.append("deprecate needs status deprecated|superseded|archived")
    elif p["type"] == "skill":
        from . import skills
        errs += skills.validate(p.get("target", ""), ch.get("skill_md") or "", p.get("trust") or "agent",
                                project=p.get("project"))
    return errs


def create(*, type: str, target: str, reason: str, evidence: list, source_sessions: list[str],
           proposed_change: dict, created_by: str, confidence: float = 0.5, risk: str = "low",
           trust: str = "agent", taint: list[str] | None = None, signature: str = "",
           project: str | None = None) -> dict:
    if type not in TYPES:
        raise ProposalError(f"bad type {type}")
    if created_by == "user":
        trust = "user"
    _check_target(type, target, project)
    # check-then-save as one step: concurrent dream cycles must not both create the same proposal
    with store._lock("proposals-create"):
        return _create(type=type, target=target, reason=reason, evidence=evidence, source_sessions=source_sessions,
                       proposed_change=proposed_change, created_by=created_by, confidence=confidence, risk=risk,
                       trust=trust, taint=taint, signature=signature, project=project)


def _create(*, type, target, reason, evidence, source_sessions, proposed_change, created_by, confidence, risk,
            trust, taint, signature, project) -> dict:
    if _suppressed(signature, project):
        raise ProposalError(f"signature {signature} was rejected in the last {SUPPRESS_DAYS} days")
    pending = list_("pending")
    dup = next((p for p in pending if signature and p.get("signature") == signature
                and (p.get("project") or None) == (project or None)), None)
    if dup:  # same pattern seen again: add evidence instead of a second proposal
        dup["evidence"] = (dup.get("evidence") or []) + [e for e in evidence if e not in dup.get("evidence", [])]
        dup["source_sessions"] = sorted(set(dup.get("source_sessions") or []) | set(source_sessions))
        dup["trust"] = D.trust_min(dup.get("trust"), trust)
        _save(dup, "pending", "proposal-evidence")
        return dup
    if project:
        if sum(1 for x in pending if x.get("project") == project) >= MAX_PENDING_PROJECT:
            raise ProposalError(f"{MAX_PENDING_PROJECT} proposals are already waiting in this project; review those first")
    elif sum(1 for x in pending if not x.get("project")) >= MAX_PENDING:
        raise ProposalError(f"{MAX_PENDING} proposals are already waiting; review those first")
    base = store.read(target) if type in ("modify", "deprecate", "merge") and target else None
    if type in ("modify", "deprecate") and not base:
        raise ProposalError(f"target {target} not found")
    p = {
        "proposal_id": f"p-{_dt.datetime.now().strftime('%Y%m%d')}-{secrets.token_hex(3)}",
        "type": type, "target": target, "reason": security.scrub(reason),
        "evidence": security.scrub_obj(evidence)[:30], "source_sessions": sorted(set(source_sessions))[:100],
        "confidence": round(float(confidence), 2), "risk": risk, "trust": trust, "taint": taint or [],
        "signature": signature, "base_hash": base.hash if base else None,
        "proposed_change": security.scrub_obj(proposed_change), "created_by": created_by,
        "created_at": D.now_iso(), "status": "pending", "project": project,
    }
    if type in ("create", "modify", "merge") and "meta" in p["proposed_change"]:
        p["proposed_change"]["meta"]["trust"] = D.trust_min(trust, p["proposed_change"]["meta"].get("trust") or "agent")
    errs = _validate_change(p)
    if errs:
        raise ProposalError("invalid proposal: " + "; ".join(errs))
    text = json.dumps(p["proposed_change"], ensure_ascii=False) + "\n" + p["reason"] + "\n" + \
        "\n".join(str(e) for e in p["evidence"])
    p["scan"] = security.scan(text)
    _save(p, "pending", "proposal")
    store.append_event("proposal_created", id=p["proposal_id"], type=type, target=target, by=created_by,
                       trust=trust, flagged=bool(p["scan"]))
    return p


def _save(p: dict, status: str, verb: str) -> None:
    path = _path(p["proposal_id"], status)
    with store._lock("store"):
        store._atomic_write(path, json.dumps(p, indent=1, ensure_ascii=False))
        for other in ("pending", "approved", "rejected"):
            q = _path(p["proposal_id"], other)
            if other != status and q.exists():
                q.unlink()
        store.commit(["proposals"], p.get("decided_by") or p["created_by"],
                     f"{verb} {p['proposal_id']} ({p['type']} {p['target']})")


def needs_confirmation(p: dict) -> list[str]:
    why = []
    if p.get("trust") == "external":
        why.append("evidence includes untrusted external content")
    if p.get("taint"):
        why.append("the source sessions read external content: " + ", ".join(p["taint"][:3]))
    if p.get("scan"):
        why.append("scanner: " + "; ".join(f"{f['severity']} {f['rule']}" for f in p["scan"]))
    if p["type"] in ("deprecate", "merge") or (p["type"] == "modify" and _target_trust(p) == "user"):
        why.append("changes or retires an existing memory")
    if p["type"] == "skill":
        why.append("installs a skill this project's workers will be offered" if p.get("project")
                   else "installs a skill every Claude session can load")
    return why


def _target_trust(p: dict) -> str | None:
    m = store.read(p["target"]) if p.get("target") and p["type"] != "skill" else None
    return m.meta.get("trust") if m else None


def approve(pid: str, *, actor: str = "user", confirm_flagged: bool = False, edit: dict | None = None,
            auto_policy: bool = False) -> dict:
    if actor != "user":
        raise store.PermissionDenied("only the user approves proposals")
    got = load(pid)
    if not got or got[1] != "pending":
        raise ProposalError(f"{pid} is not pending")
    p = got[0]
    _check_target(p["type"], p.get("target") or "", p.get("project"))
    if edit:  # the user edited the proposed text before approving: that text is now the user's
        p["proposed_change"] = edit
        p["trust"] = "user"
        p["scan"] = security.scan(json.dumps(edit, ensure_ascii=False))
    if security.blocked(p.get("scan") or []):
        raise ProposalError("blocked by the poisoning scan; edit the proposal text first")
    why = needs_confirmation(p)
    if why and not confirm_flagged:
        raise ProposalError("needs explicit confirmation: " + " | ".join(why))
    errs = _validate_change(p)
    if errs:
        raise ProposalError("invalid: " + "; ".join(errs))
    by = f"proposal:{pid}"
    ch = p["proposed_change"]
    quarantine = None if p["trust"] == "user" else (
        _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=QUARANTINE_HOURS)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    try:
        if p["type"] in ("create", "modify", "merge"):
            meta = dict(ch["meta"], approved_by="user", source_sessions=p.get("source_sessions"),
                        trust=D.trust_min(p["trust"], ch["meta"].get("trust") or "agent"))
            meta.setdefault("created_by", p["created_by"])
            if quarantine:
                meta["inject_after"] = quarantine
            cur = store.read(p["target"]) if p["type"] == "modify" else None
            if p["type"] == "modify" and (not cur or cur.hash != p.get("base_hash")):
                raise store.ConflictError("target changed since the proposal was made", cur.hash if cur else None)
            meta["id"] = p["target"]
            m = store.write(meta, ch["body"], actor=by, expected_hash=cur.hash if cur else None,
                            create_only=p["type"] != "modify", reason=p["reason"])
            for old in ch.get("supersede") or []:
                store.set_status(old, "superseded", actor=by, superseded_by=m.id, reason=f"merged into {m.id}")
        elif p["type"] == "deprecate":
            cur = store.read(p["target"])
            if not cur or cur.hash != p.get("base_hash"):
                raise store.ConflictError("target changed since the proposal was made", cur.hash if cur else None)
            store.set_status(p["target"], ch["status"], actor=by, expected_hash=cur.hash,
                             superseded_by=ch.get("superseded_by"), reason=p["reason"])
        elif p["type"] == "skill":
            from . import skills
            p["installed_to"] = skills.install(p["target"], ch["skill_md"], pid, project=p.get("project"))
    except store.ConflictError as e:
        p["status"], p["note"] = "stale", str(e)
        _save(p, "pending", "stale")
        store.append_event("proposal_stale", id=pid)
        raise ProposalError(f"stale: {e}. Re-run the dreamer or edit the proposal.")
    p.update(status="approved", decided_by="auto-policy" if auto_policy else "user", decided_at=D.now_iso())
    _save(p, "approved", "approve")
    store.append_event("proposal_approved", id=pid, type=p["type"], target=p["target"])
    return p


def reject(pid: str, *, actor: str = "user", reason: str = "", auto_policy: bool = False) -> dict:
    if actor != "user":
        raise store.PermissionDenied("only the user rejects proposals")
    got = load(pid)
    if not got or got[1] != "pending":
        raise ProposalError(f"{pid} is not pending")
    p = got[0]
    p.update(status="rejected", decided_by="auto-policy" if auto_policy else "user", decided_at=D.now_iso(), note=reason)
    _save(p, "rejected", "reject")
    store.append_event("proposal_rejected", id=pid, signature=p.get("signature"))
    return p


def diff(p: dict) -> str:
    """Unified diff of what approving would change (create: all added; deprecate: the retired text)."""
    import difflib
    ch = p.get("proposed_change") or {}
    cur = store.read(p["target"]) if p.get("target") and p["type"] != "skill" else None
    old = cur.text_raw.splitlines() if cur else []
    if p["type"] in ("create", "modify", "merge"):
        meta = dict(ch.get("meta") or {}, id=p["target"])
        new = D.render({k: v for k, v in meta.items() if v not in (None, [], "")}, ch.get("body") or "").splitlines()
    elif p["type"] == "deprecate":
        new = [ln.replace("status: active", f"status: {ch.get('status')}") for ln in old]
    elif p["type"] == "skill":
        new = (ch.get("skill_md") or "").splitlines()
    else:
        new = []
    label = p["target"] or "(new)"
    return "\n".join(difflib.unified_diff(old, new, f"current/{label}", f"proposed/{label}", lineterm=""))


_AUTO_TYPES = {"create", "modify", "deprecate", "merge"}
_DEICTIC_END = re.compile(r"\b(this|that|it|them|those|these|so|too|btw)\W*$", re.I)


def too_vague(p: dict) -> bool:
    """A title that only makes sense next to the chat it came from ("Prefers the idea of that")."""
    title = str(((p.get("proposed_change") or {}).get("meta") or {}).get("title") or "")
    words = [w for w in re.findall(r"[a-z0-9]+", title.lower()) if len(w) > 2 and w not in
             {"the", "and", "user", "prefers", "likes", "wants", "uses", "always", "never", "idea", "this", "that"}]
    return p.get("type") in ("create", "modify") and bool(title) and (len(words) < 2 or bool(_DEICTIC_END.search(title)))


def auto_approvable(p: dict, cfg: dict) -> bool:
    """Approve without a human (memory.auto_approve, on by default). The Dreamer runs on its own; this is
    the line it may not cross alone:
    - never skills (they are instructions every worker would be offered), never anything from untrusted
      external content, never a proposal the poisoning scan flagged, never a high-risk one;
    - the user's own verified words (trust "user") apply even if that chat also read web pages: the quote
      is theirs, not the page's. Agent-derived findings need untainted sessions;
    - agent text never changes or retires a memory the user wrote.
    Config lives in router.yaml (memory.auto_approve, memory.auto_approve_types), not in anything a
    browser or worker edits."""
    if not cfg.get("auto_approve"):
        return False
    types = {t.strip() for t in str(cfg.get("auto_approve_types") or "").split(",") if t.strip()}
    trust = p.get("trust")
    if p.get("type") not in types & _AUTO_TYPES or trust not in ("agent", "user") or p.get("scan"):
        return False
    if p.get("risk") == "high" or (trust == "agent" and p.get("taint")) or too_vague(p):
        return False
    if p.get("type") != "create" and trust != "user" and _target_trust(p) == "user":
        return False
    return str(p.get("target", "")).startswith(("projects/", "global/"))


def render(p: dict) -> str:
    """Plain-text review view: invisible characters made visible, nothing rendered as markdown/HTML."""
    def vis(s) -> str:
        s = json.dumps(s, ensure_ascii=False, indent=1) if not isinstance(s, str) else s
        return security._HIDDEN.sub(lambda m: f"<U+{ord(m.group(0)):04X}>", s)
    ch = p.get("proposed_change") or {}
    lines = [f"{p['proposal_id']}  [{p['type']}] → {p['target']}   status={p['status']}",
             f"by {p['created_by']} · {p['created_at']} · confidence {p.get('confidence')} · risk {p.get('risk')} · trust {p.get('trust')}",
             f"reason: {vis(p.get('reason'))}",
             f"support: {len(p.get('source_sessions') or [])} sessions"]
    for w in needs_confirmation(p):
        lines.append(f"!! {w}")
    lines.append("evidence:")
    lines += [f"  - {vis(e)}" for e in (p.get("evidence") or [])[:8]]
    lines.append("change:")
    if "body" in ch:
        lines.append("  meta: " + vis({k: v for k, v in (ch.get("meta") or {}).items()}))
        lines += ["  | " + ln for ln in vis(ch["body"]).splitlines()]
        if p["type"] == "modify":
            cur = store.read(p["target"])
            if cur:
                import difflib
                lines.append("  diff vs current:")
                lines += ["  " + ln for ln in difflib.unified_diff(cur.body.splitlines(), ch["body"].splitlines(),
                                                                   "current", "proposed", lineterm="")]
    elif "skill_md" in ch:
        lines += ["  | " + ln for ln in vis(ch["skill_md"]).splitlines()]
    else:
        lines.append("  " + vis(ch))
    if p["type"] in ("deprecate", "merge") and p.get("target"):
        cur = store.read(p["target"])
        if cur:
            lines.append("  current text being retired:")
            lines += ["  | " + ln for ln in vis(cur.body).splitlines()]
    return "\n".join(lines)
