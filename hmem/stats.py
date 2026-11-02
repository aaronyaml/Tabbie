"""Observability: counts derived from the store, proposals, traces and the append-only event log.
Also the three evaluation metrics that matter at this scale:
- retrieval recall (tests),
- recurrence: does a failure signature keep happening after a lesson about it was approved?
- uptake: do workers actually open the memory titles they are given?
"""

from __future__ import annotations

import collections
import json

from . import proposals, store, trace as T


def events(kinds: tuple[str, ...] | None = None) -> list[dict]:
    p = store.root() / ".stats" / "events.jsonl"
    out = []
    if not p.exists():
        return out
    with open(p, encoding="utf-8") as fh:
        for ln in fh:
            try:
                e = json.loads(ln)
            except ValueError:
                continue
            if not kinds or e.get("kind") in kinds:
                out.append(e)
    return out


def summary() -> dict:
    ev = events()
    by = collections.Counter(e["kind"] for e in ev)
    mems = store.all_memories(include_inactive=True)
    status = collections.Counter(m.meta.get("status") for m in mems)
    scope = collections.Counter(store.scope_of(m.id)[0] for m in mems if m.meta.get("status") == "active")
    traces = T.load_all()
    tools = collections.Counter()
    tool_err = collections.Counter()
    for t in traces:
        for k, v in (t.get("tools") or {}).items():
            tools[k] += v["calls"]
            tool_err[k] += v["errors"]
    retr = [e for e in ev if e["kind"] == "retrieval"]
    dreams = [e for e in ev if e["kind"] == "dream"]
    return {
        "memories": {"active": status.get("active", 0), "deprecated": status.get("deprecated", 0),
                     "superseded": status.get("superseded", 0), "by_scope": dict(scope)},
        "memories_created": sum(1 for e in ev if e["kind"] == "write" and e.get("created")),
        "memories_updated": sum(1 for e in ev if e["kind"] == "write" and not e.get("created")),
        "proposals": {s: len(proposals.list_(s)) for s in ("pending", "approved", "rejected")},
        "sessions_traced": len(traces),
        "outcomes": dict(collections.Counter((t.get("outcome") or {}).get("label") for t in traces)),
        "dream_cycles": len(dreams), "last_dream": dreams[-1] if dreams else None,
        "repeated_failures_detected": sum(e.get("repeated_failures", 0) for e in dreams[-1:]),
        "skills_proposed": sum(1 for e in ev if e["kind"] == "proposal_created" and e.get("type") == "skill"),
        "retrievals": len(retr), "retrieval_hit_rate": round(sum(1 for e in retr if e.get("hit")) / len(retr), 2) if retr else None,
        "memory_conflicts": by.get("proposal_stale", 0), "writes_blocked": by.get("write_blocked", 0),
        "integrity_alerts": by.get("integrity_mismatch", 0), "trace_failures": by.get("trace_failed", 0),
        "tool_failure_rates": {k: round(tool_err[k] / tools[k], 3) for k, _ in tools.most_common(12)},
        "agent_failure_rate": round(sum(1 for t in traces if (t.get("outcome") or {}).get("label") == "failure")
                                    / len(traces), 3) if traces else None,
        "uptake": uptake(traces), "recurrence": recurrence(traces),
    }


def uptake(traces: list[dict]) -> dict:
    """Of the memory titles handed to workers, how many did the worker open?"""
    given = {e.get("actor", "").split(":", 1)[-1]: set(e.get("ids") or []) for e in events(("retrieval",))
             if e.get("kind_detail") == "worker_titles" and e.get("ids")}
    offered = opened = 0
    for t in traces:
        ids = given.get(t.get("request_id") or "")
        if not ids:
            continue
        offered += len(ids)
        reads = " ".join(t.get("files_read") or []) + " " + " ".join(t.get("commands") or [])
        opened += sum(1 for i in ids if i in reads)
    return {"offered": offered, "opened": opened, "rate": round(opened / offered, 2) if offered else None}


def recurrence(traces: list[dict]) -> list[dict]:
    """For each approved failure lesson: how often its error signature occurred before vs after approval."""
    out = []
    for p in proposals.list_("approved"):
        sig = (p.get("signature") or "")
        if not sig.startswith("err:"):
            continue
        key, at = sig[4:], p.get("decided_at") or ""
        before = after = 0
        for t in traces:
            hit = any(e["sig"].startswith(key) for e in t.get("errors") or [])
            if hit:
                if (t.get("started") or "") < at:
                    before += 1
                else:
                    after += 1
        out.append({"proposal": p["proposal_id"], "target": p["target"], "before": before, "after": after})
    return out
