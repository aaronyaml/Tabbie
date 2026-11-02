"""Post-task extraction: cheap, deterministic, runs right after a request finishes.

It does NOT write memory. It looks at one trace and records lesson *candidates* next to it
(sessions/…/lessons.json) for the Dreamer, which only promotes patterns that recur across
conversations. Most traces produce nothing.

Candidates:
- directive     the user stated a standing preference/rule in their own words ("from now on…")
- correction    the user's next message corrected the result
- failure       the run failed / reported a problem
- repeated_error the same error signature hit ≥2 times inside this run
- resolved_error an error followed later by a successful call of the same tool (what fixed it)
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import store, trace as T

# The user explicitly asking for something to be kept: counts on its own.
EXPLICIT = re.compile(r"(?i)\b(remember (that|this|to)|from now on|going forward|in (the )?future,|"
                      r"note (that|this) for|for future (reference|tasks|films|runs)|always remember|"
                      r"keep (a )?note (that|for future))\b")
# Standing-rule phrasing: needs to recur across conversations before it counts.
STANDING = re.compile(r"(?i)\b(always|never|don'?t ever|make sure (to|it|that)|every (time|single)|each time|"
                      r"i (want|prefer|hate|don'?t want|like) |it should (always|never|only)|should never|"
                      r"should always|do not|don'?t)\b")
_PASTE = re.compile(r"<pasted_content[^>]*>.*?</pasted_content[^>]*>|```.*?```", re.S)


def user_words(text: str) -> str:
    """The user's own typing: pasted blocks and code fences removed (those are other people's words)."""
    return re.sub(r"\s+", " ", _PASTE.sub(" ", text or "")).strip()


def sentences_with(rx: re.Pattern, text: str) -> list[str]:
    out = []
    for s in re.split(r"(?<=[.!?\n])\s+", text):
        s = s.strip()
        if 12 <= len(s) <= 400 and rx.search(s):
            out.append(s)
    return out


def user_texts(t: dict) -> list[str]:
    if t["source"] == "hermes":
        txts = []
        if not t.get("studio_job"):
            txts.append(t.get("user_request") or "")
        elif "[studio:chat]" in (t.get("user_request") or ""):
            m = re.search(r"The user says: (.*)$", t["user_request"], re.S)
            if m:
                txts.append(m.group(1))
        if t.get("feedback", {}).get("next_message"):
            txts.append(t["feedback"]["next_message"])
        return txts
    return [m["text"] for m in t.get("user_messages") or []]


def extract(t: dict) -> list[dict]:
    c = []
    for raw in user_texts(t):
        words = user_words(raw)
        for s in sentences_with(EXPLICIT, words):
            c.append({"kind": "directive", "explicit": True, "quote": s})
        for s in sentences_with(STANDING, words):
            if not EXPLICIT.search(s):
                c.append({"kind": "directive", "explicit": False, "quote": s})
    fb = t.get("feedback") or {}
    if fb.get("class") == "correction" and fb.get("next_message"):
        c.append({"kind": "correction", "quote": user_words(fb["next_message"])[:400]})
    for m in t.get("user_messages") or []:
        if m.get("class") == "correction":
            c.append({"kind": "correction", "quote": user_words(m["text"])[:400]})
    if (t.get("outcome") or {}).get("label") == "failure" and t["source"] == "hermes":
        c.append({"kind": "failure", "quote": (t.get("error") or t.get("final_result") or "")[:300],
                  "signals": t["outcome"].get("signals")})
    sigs: dict[str, int] = {}
    for e in t.get("errors") or []:
        sigs[e["sig"]] = sigs.get(e["sig"], 0) + 1
    for sig, n in sigs.items():
        if n >= 2:
            c.append({"kind": "repeated_error", "sig": sig, "count": n})
    c += resolutions(t)
    seen, out = set(), []
    for x in c:
        k = (x["kind"], x.get("quote") or x.get("sig"))
        if x.get("quote") and any(y["kind"] == x["kind"] and y.get("quote") and
                                  (x["quote"] in y["quote"] or y["quote"] in x["quote"]) for y in out):
            continue  # the same sentence matched twice (overlapping splits)
        if k not in seen and (x.get("quote") or x.get("sig")):
            seen.add(k)
            out.append(x)
    return out


def resolutions(t: dict) -> list[dict]:
    """Error → the next successful call of the same tool (often the fix)."""
    out = []
    steps = t.get("steps") or []
    for i, s in enumerate(steps):
        if s.get("ok") is False and s.get("sig"):
            for nxt in steps[i + 1:i + 8]:
                if nxt.get("tool") == s["tool"] and nxt.get("ok"):
                    out.append({"kind": "resolved_error", "sig": s["sig"], "tool": s["tool"],
                                "failed": (s.get("cmd") or s.get("input") or s.get("file") or "")[:200],
                                "fixed_by": (nxt.get("cmd") or nxt.get("input") or nxt.get("file") or "")[:200]})
                    break
    return out


def run_for(trace_path: Path) -> list[dict]:
    t = json.loads(trace_path.read_text(encoding="utf-8"))
    cands = extract(t)
    out = trace_path.parent / "lessons.json"
    if cands:
        store._atomic_write(out, json.dumps({"trace": t["id"], "taint": t.get("taint") or [], "candidates": cands},
                                            ensure_ascii=False, indent=1))
    elif out.exists():
        out.unlink()
    return cands


def after_request(request_dir: Path) -> dict:
    """Hook entry: trace one finished request, extract candidates, maybe trigger a dream. Never raises."""
    try:
        store.init()
        reqs = []
        rd = request_dir.parent
        for d in sorted(rd.iterdir())[-200:]:
            r = T._read_json(d / "request.json")
            if r:
                reqs.append(r)
        t = T.build_hermes(request_dir, reqs)
        if not t:
            return {"ok": False, "why": "not finished"}
        d = T.save(t)
        cands = run_for(d / "trace.json")
        pid = t.get("project_id")
        store.append_event("trace", id=t["id"], outcome=t["outcome"]["label"], candidates=len(cands), project=pid)
        from . import dreamer
        if pid:
            dreamer.maybe_trigger("after_session", pid)
        dreamer.maybe_trigger("after_session")
        return {"ok": True, "trace": str(d), "candidates": len(cands)}
    except Exception as e:  # tracing must never break the user's request
        try:
            store.append_event("trace_failed", request=str(request_dir.name), error=str(e)[:300])
        except Exception:
            pass
        return {"ok": False, "error": str(e)}
