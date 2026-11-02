"""Session traces: the evidence the learning system works from.

Three sources, one format (memory/sessions/YYYY/MM/<trace-id>/trace.json + summary.md):
- "hermes": one trace per command-centre request (state/requests/<id>/), its Claude transcript
  slice ([started, ended] of the run, so resumed sessions don't bleed into each other), agents,
  outcome and the user's next message in that conversation.
- "claude-code": one trace per direct Claude Code conversation (~/.claude/projects/*/<sid>.jsonl)
  that is not already covered by a Hermes request.
- "webui": one trace per Hermes WebUI hub chat (~/.hermes/webui/sessions/<sid>.json): only the
  user's own messages (the hub's replies and tool calls are not evidence of what the user wants).
  Its conversation key is `hermes:<sid>`, the same as that chat's worker requests, so support
  counts never double up.

Safety: transcripts are streamed line by line; results from credential paths and env-dump commands
are dropped entirely; everything is scrubbed (patterns + this machine's actual secret values) BEFORE
truncation; traces are capped (MAX_TRACE_BYTES). Taint records whether the run took in external
content (web, browser, MCP, downloads/attachments): anything learned from a tainted run is external.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
from pathlib import Path

from . import security, store

HERMES = Path(os.environ.get("COMMAND_CENTRE_HOME", Path.home() / "hermes")).expanduser()
CLAUDE_PROJECTS = Path(os.environ.get("HMEM_CLAUDE_PROJECTS", Path.home() / ".claude" / "projects")).expanduser()
# Hermes WebUI chats with the hub (one JSON file per session). Most hub turns never become a worker
# request, so without this source the user's own words in a project chat are never seen.
WEBUI_SESSIONS = Path(os.environ.get("HMEM_WEBUI_SESSIONS", Path.home() / ".hermes" / "webui" / "sessions")).expanduser()
MAX_TRACE_BYTES = 200_000
MAX_LINE = 1_500_000  # longer transcript lines are images/blobs: skipped
RESULT_CHARS = 400
_MSG_LINE = re.compile(rb'"type":\s*"(user|assistant)"')

_ENV_DUMP = re.compile(r"(?i)(^|[;&|]\s*)(env|printenv|set|export -p|security\s+find-[a-z-]*password|gh auth token|"
                       r"op read|aws configure export-credentials|gcloud auth print-|vercel env pull|supabase status|"
                       r"heroku config|docker inspect|cat\s+[^|;&]*(\.env|credentials|auth\.json|id_rsa|\.pem|\.netrc|token))\b")
_TEST_CMD = re.compile(r"(?i)\b(pytest|npm (run )?test|vitest|jest|go test|cargo test|xcodebuild test|npm run verify|"
                       r"npm run (lint|typecheck|build)|tsc\b|node --check)")
_NOISE_CMD = re.compile(r"^\s*(rg|grep|egrep|ls|test|\[|find|which|command -v|pgrep|diff|cmp|stat|file)\b")
_WEB_TOOLS = re.compile(r"^(WebFetch|WebSearch|mcp__claude-in-chrome__|mcp__.*(browser|chrome|playwright|fetch|web))")
CORRECTION = re.compile(r"(?i)\b(wtf|wrong|not what|didn'?t|doesn'?t|isn'?t|still (not|broken|doesn)|broken|"
                        r"should(n'?t)? (be|have)|instead|why (did|does|is)|undo|revert|stop|ugh|bro\b|no,|nope|"
                        r"that'?s not|never|it says|fix (this|it|that))")
THANKS = re.compile(r"(?i)\b(thanks|thank you|perfect|great|nice|works|love it|awesome|cheers|legend|sick)\b")
_SYS_USER = ("<system-reminder", "<command-", "<local-command", "Caveat:", "[Request interrupted", "<task-notification",
             "This session is being continued", "<bash-", "<user-prompt-submit-hook")


# ------------------------------------------------------------------ secrets known on this machine

_KNOWN: dict = {"values": None}


def _known_secrets() -> list[str]:
    """Values of secret-looking variables in this machine's env files and environment. Used only in
    memory to redact exact occurrences; never written anywhere."""
    if _KNOWN["values"] is None:
        vals = set()
        files = [Path.home() / ".hermes" / ".env", HERMES / "hermes-webui" / ".env"]
        pairs = list(os.environ.items())
        for f in files:
            try:
                pairs += [ln.split("=", 1) for ln in f.read_text().splitlines() if "=" in ln and not ln.startswith("#")]
            except OSError:
                pass
        for k, v in pairs:
            v = v.strip().strip("'\"")
            if len(v) >= 10 and re.search(r"(?i)key|token|secret|pass|auth|cookie|session|pwd", k) \
                    and not v.startswith(("/", "http://localhost", "~")):
                vals.add(v)
        _KNOWN["values"] = sorted(vals, key=len, reverse=True)
    return _KNOWN["values"]


def clean(text, n: int | None = None) -> str:
    s = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False, default=str)
    if n:  # keep a window well beyond the excerpt: a secret cut at the window edge can't reach the kept part
        s = s[:max(n * 4, 8000)]
    for v in _known_secrets():
        if v in s:
            s = s.replace(v, security.REDACTED)
    s = security.scrub(s)
    if n and len(s) > n:
        s = s[:n] + f"…[+{len(s) - n}]"
    return s


_IGNORED_ERRORS = re.compile(r"(?i)doesn'?t want to proceed|user (rejected|denied|declined)|interrupted by user|"
                             r"file has not been read yet|was modified since it was read")


def _key_line(text: str) -> str:
    """The informative line of an error: drop the 'Exit code N' header; for tracebacks the exception line."""
    lines = [ln.strip() for ln in (text or "")[:4000].splitlines() if ln.strip()]
    lines = [ln for ln in lines if not re.fullmatch(r"(?i)exit code -?\d+|<\/?tool_use_error>", ln)]
    if any(ln.startswith("Traceback") for ln in lines):
        exc = [ln for ln in lines if re.match(r"^[\w.]+(Error|Exception|Exit|Interrupt)\b", ln)]
        if exc:
            return exc[-1]
    return lines[0] if lines else ""


def meaningful_error(text: str) -> bool:
    k = _key_line(text)
    return bool(k) and not _IGNORED_ERRORS.search(text or "") and len(re.sub(r"[^a-z]", "", k.lower())) >= 15


def err_signature(text: str) -> str:
    """Normalise an error so the same failure in different sessions groups together."""
    t = _key_line(text)[:600].lower()
    t = re.sub(r"(/[\w.@~+-]+)+", "<path>", t)
    t = re.sub(r"0x[0-9a-f]+|\b[0-9a-f]{7,}\b|\d+(\.\d+)?", "<n>", t)
    t = re.sub(r"(['\"`])([^'\"`]{0,200})\1", lambda m: m.group(0) if re.fullmatch(r"[\w.-]{1,30}", m.group(2)) else "<s>", t)
    t = re.sub(r"\s+", " ", t).strip()[:140]
    return hashlib.sha1(t.encode()).hexdigest()[:10] + " " + t[:90]


def classify_feedback(text: str) -> str:
    if not text:
        return "none"
    t = text.strip()[:600]
    if CORRECTION.search(t):
        return "correction"
    if THANKS.search(t) and len(t) < 200:
        return "thanks"
    return "new"


def _ts(s) -> float | None:
    if isinstance(s, (int, float)):
        return float(s)
    try:
        return _dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def _iso(t: float | None) -> str | None:
    return _dt.datetime.fromtimestamp(t, _dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z") if t else None


def transcript_path(cwd: str, session_id: str) -> Path:
    return CLAUDE_PROJECTS / re.sub(r"[^A-Za-z0-9]", "-", cwd) / f"{session_id}.jsonl"


# ------------------------------------------------------------------ transcript parsing

def _user_text(m: dict) -> str | None:
    c = m.get("content")
    if isinstance(c, str):
        txt = c
    elif isinstance(c, list):
        if any(b.get("type") == "tool_result" for b in c if isinstance(b, dict)):
            return None
        txt = "\n".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    else:
        return None
    txt = txt.strip()
    if not txt or txt.startswith(_SYS_USER):
        return None
    return txt


def _typed_by_user(e: dict) -> bool:
    """Only what the human typed counts as the user's words: not skill text, task notifications, messages
    from other agents, harness notices, or prompts sent by programs (claude -p / SDK)."""
    if e.get("isMeta"):
        return False
    origin = e.get("origin")
    if isinstance(origin, dict):
        return origin.get("kind") == "human"
    return e.get("entrypoint") == "cli"  # older transcripts: no origin field


def parse_transcript(path: Path, t0: float | None = None, t1: float | None = None, sidechain: bool = False) -> dict:
    """Stream one transcript (optionally only events in [t0, t1]) into trace fields."""
    out = {"steps": [], "tools": {}, "files_read": [], "files_modified": [], "commands": [], "tests": [],
           "errors": [], "retries": 0, "user_messages": [], "taint": [], "models": [], "output_tokens": 0,
           "first": None, "last": None, "final_text": "", "cwd": None, "session_id": None}
    pending: dict[str, dict] = {}
    last_err_tool = None
    try:
        fh = open(path, "rb")
    except OSError:
        return out
    with fh:
        for raw in fh:
            if len(raw) > MAX_LINE or not _MSG_LINE.search(raw):
                continue
            try:
                e = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                continue
            t = _ts(e.get("timestamp"))
            if t is not None and ((t0 and t < t0 - 2) or (t1 and t > t1 + 2)):
                continue
            if e.get("type") not in ("user", "assistant"):
                continue
            out["cwd"] = out["cwd"] or e.get("cwd")
            out["session_id"] = out["session_id"] or e.get("sessionId")
            if t:
                out["first"] = out["first"] or t
                out["last"] = t
            m = e.get("message") or {}
            side = sidechain or bool(e.get("isSidechain"))
            if e["type"] == "assistant":
                if m.get("model") and m["model"] not in out["models"] and not m["model"].startswith("<"):
                    out["models"].append(m["model"])
                out["output_tokens"] += int((m.get("usage") or {}).get("output_tokens") or 0)
                for b in m.get("content") or []:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "text" and not side and b.get("text", "").strip():
                        out["final_text"] = b["text"]
                    if b.get("type") != "tool_use":
                        continue
                    name, inp = b.get("name", "?"), b.get("input") or {}
                    step = {"t": _iso(t), "tool": name, "side": side or None}
                    fp = inp.get("file_path") or inp.get("notebook_path") or inp.get("path")
                    if isinstance(fp, str):
                        step["file"] = fp
                        if name in ("Read", "Grep", "Glob") and fp not in out["files_read"]:
                            out["files_read"].append(fp)
                        if name in ("Edit", "Write", "MultiEdit", "NotebookEdit") and fp not in out["files_modified"]:
                            out["files_modified"].append(fp)
                        if security.is_credential_path(fp):
                            step["credential"] = True
                    if name == "Bash":
                        cmd = str(inp.get("command", ""))
                        step["cmd"] = clean(cmd, 300)
                        out["commands"].append(step["cmd"])
                        if _ENV_DUMP.search(cmd):
                            step["credential"] = True
                    elif name not in ("Read", "Edit", "Write", "MultiEdit"):
                        step["input"] = clean(inp, 200)
                    if _WEB_TOOLS.match(name):
                        origin = inp.get("url") or inp.get("query") or name
                        host = re.sub(r"^https?://([^/]+).*", r"\1", str(origin))[:80]
                        tag = f"{name.split('__')[1] if '__' in name else name}:{host}"
                        if tag not in out["taint"]:
                            out["taint"].append(tag)
                    if isinstance(fp, str) and re.search(r"/Downloads/|^/tmp/|^/private/tmp/", fp) \
                            and name == "Read" and f"file:{Path(fp).name}" not in out["taint"]:
                        out["taint"].append(f"file:{Path(fp).name[:60]}")
                    out["tools"].setdefault(name, {"calls": 0, "errors": 0})["calls"] += 1
                    pending[b.get("id")] = step
                    out["steps"].append(step)
            else:
                c = m.get("content")
                if isinstance(c, list):
                    for b in c:
                        if not isinstance(b, dict) or b.get("type") != "tool_result":
                            continue
                        step = pending.pop(b.get("tool_use_id"), None)
                        if not step:
                            continue
                        res = b.get("content")
                        if isinstance(res, list):
                            res = "\n".join(x.get("text", "") for x in res if isinstance(x, dict))
                        is_err = bool(b.get("is_error"))
                        step["ok"] = not is_err
                        if step.get("credential"):
                            step["result"] = "[redacted: credential path or env dump]"
                        else:
                            step["result"] = clean(res or "", RESULT_CHARS)
                        noise = step["tool"] == "Bash" and _NOISE_CMD.match(step.get("cmd") or "")
                        if is_err:
                            out["tools"][step["tool"]]["errors"] += 1
                            if not noise and meaningful_error(str(res or "")):
                                sig = err_signature(str(res or ""))
                                step["sig"] = sig
                                out["errors"].append({"tool": step["tool"], "sig": sig,
                                                      "text": step["result"][:300], "t": step["t"]})
                            if last_err_tool == step["tool"]:
                                out["retries"] += 1
                            last_err_tool = step["tool"]
                        else:
                            last_err_tool = None
                        if step["tool"] == "Bash" and _TEST_CMD.search(step.get("cmd") or ""):
                            out["tests"].append({"cmd": step["cmd"][:160], "ok": not is_err})
                elif not side and _typed_by_user(e):
                    txt = _user_text(m)
                    if txt:
                        out["user_messages"].append({"t": _iso(t), "text": clean(txt, 600),
                                                     "class": classify_feedback(txt)})
    return out


def _subagent_files(path: Path) -> list[Path]:
    d = path.with_suffix("") / "subagents"
    return sorted(d.glob("*.jsonl")) if d.is_dir() else []


def _merge_side(base: dict, side: dict) -> None:
    for k in ("steps", "files_read", "files_modified", "commands", "tests", "errors", "taint"):
        for x in side[k]:
            if k in ("files_read", "files_modified", "taint") and x in base[k]:
                continue
            base[k].append(x)
    for name, v in side["tools"].items():
        b = base["tools"].setdefault(name, {"calls": 0, "errors": 0})
        b["calls"] += v["calls"]
        b["errors"] += v["errors"]
    base["retries"] += side["retries"]
    base["output_tokens"] += side["output_tokens"]


# ------------------------------------------------------------------ outcome

def outcome(ok: bool | None, final_text: str, feedback_class: str, *, asked: bool = False,
            re_requested: bool = False, status: str = "") -> dict:
    signals = []
    label, conf = "unknown", 0.3
    last = (final_text or "").strip().splitlines()[-1:] or [""]
    if ok is False or status in ("failed", "claude_limited"):
        label, conf = "failure", 0.8
        signals.append(f"run {status or 'failed'}")
    elif re.match(r"(?i)\s*(problem|failed|could not|couldn'?t)", last[0]) or re.search(r"(?im)^\s*problem:", final_text or ""):
        label, conf = "failure", 0.6
        signals.append("worker reported a problem")
    elif ok:
        label, conf = "success", 0.5
        signals.append("run ok")
    if asked:
        signals.append("asked the user a question")
    if feedback_class == "correction":
        label, conf = "failure", 0.75
        signals.append("user corrected the result")
    elif feedback_class == "thanks" and label != "failure":
        label, conf = "success", 0.8
        signals.append("user was satisfied")
    if re_requested:
        label, conf = "failure", max(conf, 0.7)
        signals.append("same task requested again within 24 h")
    return {"label": label, "confidence": conf, "signals": signals, "source": "heuristic"}


# ------------------------------------------------------------------ build + save

def _cap(trace: dict) -> dict:
    """Bound a trace to MAX_TRACE_BYTES. Every pass shrinks something, so this always terminates."""
    limits = {"commands": 300, "files_read": 300, "files_modified": 300, "errors": 200, "user_messages": 150,
              "tests": 100, "taint": 50}
    for k, n in limits.items():
        if len(trace.get(k) or []) > n:
            trace[k] = trace[k][:n // 2] + trace[k][-n // 2:]
    size = lambda: len(json.dumps(trace, ensure_ascii=False))  # noqa: E731
    while size() > MAX_TRACE_BYTES:
        steps = trace.get("steps") or []
        if len(steps) > 40:
            keep = len(steps) // 2
            trace["steps"] = steps[:keep // 2] + [{"tool": "…", "result": f"{len(steps) - keep} steps omitted"}] \
                + steps[-keep // 2:]
            continue
        biggest = max(("steps", "user_messages", "commands", "files_read", "errors"), key=lambda k: len(json.dumps(trace.get(k) or [])))
        lst = trace.get(biggest) or []
        if len(lst) <= 4:
            for st in trace.get("steps") or []:
                st.pop("input", None)
                st["result"] = (st.get("result") or "")[:120]
            trace["user_request"] = (trace.get("user_request") or "")[:1500]
            trace["final_result"] = (trace.get("final_result") or "")[:1500]
            break
        trace[biggest] = lst[:len(lst) // 4] + lst[-len(lst) // 4:]
    return trace


def trace_dir(trace_id: str, started: float | None) -> Path:
    d = _dt.datetime.fromtimestamp(started or 0)
    return store.root() / "sessions" / f"{d:%Y}" / f"{d:%m}" / trace_id


def save(trace: dict) -> Path:
    trace = _cap(attribute(trace))
    d = trace_dir(trace["id"], _ts(trace.get("started")))
    sessions = store.root() / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    os.chmod(sessions, 0o700)
    d.mkdir(parents=True, exist_ok=True)
    with store._lock("traces"):  # claim_history may rewrite the same file
        store._atomic_write(d / "trace.json", json.dumps(trace, ensure_ascii=False, indent=1))
        store._atomic_write(d / "summary.md", summary_md(trace))
    return d


def summary_md(t: dict) -> str:
    o = t.get("outcome") or {}
    tools = ", ".join(f"{k}×{v['calls']}" + (f" ({v['errors']} err)" if v["errors"] else "")
                      for k, v in sorted(t.get("tools", {}).items(), key=lambda x: -x[1]["calls"])[:10])
    lines = [f"# {t['id']} ({t['source']})", "",
             f"- project: {t.get('project') or '-'} · conversation: {t.get('conversation')}",
             f"- when: {t.get('started')} → {t.get('ended')} ({t.get('duration_s')} s)",
             f"- outcome: {o.get('label')} ({', '.join(o.get('signals') or [])})",
             f"- tools: {tools or '-'}", f"- files modified: {len(t.get('files_modified') or [])} · errors: "
             f"{len(t.get('errors') or [])} · retries: {t.get('retries')}",
             f"- taint: {', '.join(t.get('taint') or []) or 'none'}", "", "## Request", "",
             (t.get("user_request") or "")[:1500], "", "## Result", "", (t.get("final_result") or "")[:1500]]
    if t.get("feedback", {}).get("next_message"):
        lines += ["", f"## Next user message ({t['feedback']['class']})", "", t["feedback"]["next_message"]]
    return "\n".join(lines) + "\n"


def _tokens_overlap(a: str, b: str) -> float:
    ta, tb = set(re.findall(r"[a-z0-9]{3,}", a.lower())), set(re.findall(r"[a-z0-9]{3,}", b.lower()))
    return len(ta & tb) / max(1, len(ta | tb))


def _studio_user_text(raw: str) -> str | None:
    """Motion studio dispatches are automated; only their [studio:chat] carries the user's words."""
    if raw.startswith("/motion [studio:"):
        m = re.search(r"The user says: (.*)$", raw, re.S)
        return m.group(1).strip() if m and "[studio:chat]" in raw else None
    return raw


def build_hermes(req_dir: Path, all_reqs: list[dict] | None = None) -> dict | None:
    r = _read_json(req_dir / "request.json")
    if not r or r.get("status") in ("planning", "awaiting_answers", "awaiting_approval", "executing", "routed", None):
        return None
    ag = _read_json(req_dir / "agents.json") or {}
    mains = [a for a in ag.get("agents", []) if a.get("parent") is None]
    started = min((a.get("started") for a in mains if a.get("started")), default=None) or _ts(r.get("created"))
    ended = max((a.get("ended") for a in mains if a.get("ended")), default=None) or _ts(r.get("updated"))
    ex = r.get("executor") or {}
    res = r.get("result") or {}
    cwd = r.get("project") or str((req_dir / "work").resolve())
    parsed = {"steps": [], "tools": {}, "files_read": [], "files_modified": [], "commands": [], "tests": [],
              "errors": [], "retries": 0, "user_messages": [], "taint": [], "models": [], "output_tokens": 0,
              "final_text": ""}
    sid = ex.get("session_id")
    if sid:
        tp = transcript_path(cwd, sid)
        if tp.exists():
            parsed = parse_transcript(tp, started, ended)
            for sf in _subagent_files(tp):
                _merge_side(parsed, parse_transcript(sf, started, ended, sidechain=True))
    # The user's own attachments are user input (authenticated channel), not external content.
    # the user's next message in this conversation (feedback) + "asked again" detection
    later = sorted((x for x in (all_reqs or []) if x.get("session_id") == r.get("session_id")
                    and (x.get("created") or "") > (r.get("created") or "")), key=lambda x: x.get("created", ""))
    nxt, nxt_text = None, None
    for x in later:
        txt = _studio_user_text(x.get("raw_message") or "")
        if txt is None:
            continue
        nxt, nxt_text = x, txt
        break
    fb_class = "none"
    if nxt and (_ts(nxt.get("created")) or 0) - (ended or 0) < 1800:
        fb_class = classify_feedback(nxt_text)
    mine = _studio_user_text(r.get("raw_message") or "") or ""
    re_req = any(x is not r and len(mine) > 20 and _tokens_overlap(mine, _studio_user_text(x.get("raw_message") or "") or "") > 0.6
                 and 0 < (_ts(x.get("created")) or 0) - (_ts(r.get("created")) or 0) < 86400
                 for x in (all_reqs or []) if x.get("session_id") == r.get("session_id"))
    final = res.get("result") or parsed["final_text"] or ""
    trace = {
        "schema": "hmem.trace/1", "id": f"h-{r['id']}", "source": "hermes", "request_id": r["id"],
        "conversation": f"hermes:{r.get('session_id')}",
        "project": r.get("project_id") or project_name(r.get("project")), "cwd": cwd,
        "started": _iso(started), "ended": _iso(ended),
        "duration_s": res.get("duration_s") or (round(ended - started, 1) if started and ended else None),
        "route": r.get("route"), "route_reason": r.get("route_reason"),
        "user_request": clean(r.get("raw_message") or r.get("original_prompt") or "", 4000),
        "studio_job": bool((r.get("raw_message") or "").startswith("/motion [studio:")),
        "executor": {k: ex.get(k) for k in ("provider", "model", "effort", "session_id", "resumed")},
        "models": parsed["models"], "agents": [{k: a.get(k) for k in ("id", "parent", "name", "status", "tokens",
                                                                     "duration_s", "tool_uses")} for a in ag.get("agents", [])],
        "tokens": sum(int(a.get("tokens") or 0) for a in mains), "output_tokens": parsed["output_tokens"],
        **{k: parsed[k] for k in ("steps", "tools", "files_read", "files_modified", "commands", "tests", "errors",
                                  "retries", "taint")},
        "final_result": clean(final, 3000), "ok": res.get("ok"), "error": clean(res.get("error") or "", 800),
        "permission_denials": len(res.get("permission_denials") or []),
        "question_asked": bool(r.get("question_log") or r.get("question")),
        "feedback": {"next_message": clean(nxt_text or "", 600) if fb_class != "none" else "", "class": fb_class,
                     "next_request": nxt.get("id") if nxt else None},
    }
    trace["outcome"] = outcome(res.get("ok"), final, fb_class, asked=trace["question_asked"], re_requested=re_req,
                               status=r.get("status", ""))
    return trace


def build_claude_code(path: Path) -> dict | None:
    p = parse_transcript(path)
    for sf in _subagent_files(path):
        _merge_side(p, parse_transcript(sf, sidechain=True))
    if not p["user_messages"] and not p["steps"]:
        return None
    msgs = p["user_messages"]
    corrections = [m for m in msgs if m["class"] == "correction"]
    fb = "correction" if corrections else ("thanks" if any(m["class"] == "thanks" for m in msgs) else "none")
    trace = {
        "schema": "hmem.trace/1", "id": f"c-{path.stem[:13]}", "source": "claude-code",
        "conversation": f"claude:{path.stem}", "project": project_name(p["cwd"]), "cwd": p["cwd"],
        "started": _iso(p["first"]), "ended": _iso(p["last"]),
        "duration_s": round(p["last"] - p["first"], 1) if p["first"] and p["last"] else None,
        "user_request": msgs[0]["text"] if msgs else "", "user_messages": msgs[:200],
        "models": p["models"], "output_tokens": p["output_tokens"],
        **{k: p[k] for k in ("steps", "tools", "files_read", "files_modified", "commands", "tests", "errors",
                             "retries", "taint")},
        "final_result": clean(p["final_text"], 3000), "ok": None,
        "feedback": {"class": fb, "corrections": len(corrections)},
    }
    trace["outcome"] = outcome(None, p["final_text"], fb)
    return trace


_OOB = re.compile(r"^\[OUT-OF-BAND USER MESSAGE[^\]]*\]\s*(.*?)\s*\[/OUT-OF-BAND USER MESSAGE\]\s*$", re.S)
_WEBUI_ATTACH = re.compile(r"\n*\[Attached files[^\]]*\][\s\S]*$")


def _webui_user_text(m: dict) -> str | None:
    """What the person typed in one WebUI message (None = not theirs / nothing to learn from)."""
    if not isinstance(m, dict) or m.get("role") != "user":
        return None
    c = m.get("content")
    if isinstance(c, list):
        c = "\n".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") in (None, "text", "input_text"))
    if not isinstance(c, str):
        return None
    txt = c.strip()
    oob = _OOB.match(txt)
    if oob:  # a steer delivered mid-turn: the user's words are inside the wrapper
        txt = oob.group(1).strip()
    txt = _studio_user_text(txt) or ""  # automated studio dispatches carry no user words
    txt = _WEBUI_ATTACH.sub("", txt).strip()
    if not txt or txt.startswith(_SYS_USER):
        return None
    return txt


def _webui_project_spaces(sess: dict) -> list[str]:
    """The workspaces of the session's WebUI project (WebUI's own grouping), if it has one."""
    pid = sess.get("project_id")
    if not pid:
        return []
    for p in _read_json(WEBUI_SESSIONS.parent / "projects.json") or []:
        if isinstance(p, dict) and p.get("project_id") == pid:
            return [str(w) for w in (p.get("workspaces") or [])] + ([str(p["workspace"])] if p.get("workspace") else [])
    return []


def build_webui(path: Path, request_projects: dict | None = None) -> dict | None:
    """One WebUI hub chat → a trace of the user's messages. The project comes from the chat's workspace
    (a linked folder or ~/hermes/workspaces/<id>), else its WebUI project's workspaces, else the single
    project its worker requests ran in (a General chat that routed work to one project)."""
    sess = _read_json(path)
    if not isinstance(sess, dict) or not sess.get("session_id"):
        return None
    sid = str(sess["session_id"])
    msgs = []
    for m in sess.get("messages") or []:
        txt = _webui_user_text(m)
        if txt:
            t = _ts(m.get("timestamp"))
            msgs.append({"t": _iso(t), "text": clean(txt, 1200), "class": classify_feedback(txt), "_ts": t})
    if not msgs:
        return None
    from . import projects
    ws = str(sess.get("workspace") or "")
    general = os.path.realpath(str(HERMES / "general"))
    rws = os.path.realpath(os.path.expanduser(ws)) if ws else ""
    in_general = not ws or rws == general or rws.startswith(general + os.sep)  # General = no project
    pid = None if in_general else projects.project_for_path(ws)
    for sp in ([] if pid else _webui_project_spaces(sess)):
        pid = projects.project_for_path(sp)
        if pid:
            break
    if not pid:
        owners = {x for x in (request_projects or {}).get(sid, set()) if x}
        pid = owners.pop() if len(owners) == 1 else None
    project = pid or (None if in_general else project_name(ws))
    stamps = [m["_ts"] for m in msgs if m["_ts"]]
    first = min(stamps, default=None) or _ts(sess.get("created_at"))
    last = max([*stamps, _ts(sess.get("last_message_at")) or 0], default=None) or _ts(sess.get("updated_at"))
    for m in msgs:
        m.pop("_ts", None)
    corrections = sum(1 for m in msgs if m["class"] == "correction")
    trace = {
        "schema": "hmem.trace/1", "id": f"w-{sid[:24]}", "source": "webui", "conversation": f"hermes:{sid}",
        "project": project, "cwd": None if in_general else ws, "started": _iso(first), "ended": _iso(last),
        "duration_s": round(last - first, 1) if first and last else None,
        "user_request": msgs[0]["text"], "user_messages": msgs[:200], "title": clean(str(sess.get("title") or ""), 120),
        "models": [x for x in [sess.get("model")] if x], "output_tokens": 0,
        "steps": [], "tools": {}, "files_read": [], "files_modified": [], "commands": [], "tests": [], "errors": [],
        "retries": 0, "taint": [], "final_result": "", "ok": None,
        "feedback": {"class": "correction" if corrections else "none", "corrections": corrections},
    }
    trace["outcome"] = outcome(None, "", trace["feedback"]["class"])
    if pid:
        trace["project"] = pid
    return trace


def webui_session_files() -> list[Path]:
    if not WEBUI_SESSIONS.is_dir():
        return []
    return sorted(p for p in WEBUI_SESSIONS.glob("*.json") if re.fullmatch(r"[0-9a-zA-Z_-]{6,64}", p.stem)
                  and not p.stem.startswith("_"))


_PROJECTS: dict = {"map": None}


def project_name(path: str | None) -> str | None:
    """Project key for a path: the id of the Project that links this folder, else the legacy key."""
    if not path:
        return None
    from . import projects
    pid = projects.project_for_path(path)
    if pid:
        return pid
    return legacy_project_name(path)


def legacy_project_name(path: str | None) -> str | None:
    """Pre-Projects key: config/projects.yaml name (longest prefix), else the folder name under home."""
    if not path:
        return None
    if _PROJECTS["map"] is None:
        m = {}
        try:
            for ln in (HERMES / "config" / "projects.yaml").read_text().splitlines():
                mm = re.match(r"^\s{2}([\w.-]+):\s*(\S+)\s*$", ln)
                if mm:
                    m[os.path.realpath(os.path.expanduser(mm.group(2)))] = mm.group(1)
        except OSError:
            pass
        _PROJECTS["map"] = m
    rp = os.path.realpath(os.path.expanduser(path))
    if rp.startswith(os.path.realpath(str(HERMES / "state" / "requests"))):
        return None  # a General-chat scratch folder: no project
    best = max((k for k in _PROJECTS["map"] if rp == k or rp.startswith(k + os.sep)), key=len, default=None)
    if best:
        name = _PROJECTS["map"][best]
        return None if name == "test-project" else re.sub(r"[^a-z0-9._-]", "-", name.lower())
    home = os.path.realpath(os.path.expanduser("~"))
    if rp == home:
        return None
    rel = rp[len(home) + 1:].split(os.sep)[0] if rp.startswith(home + os.sep) else Path(rp).name
    return re.sub(r"[^a-z0-9._-]", "-", rel.lower()) or None


def _read_json(p: Path):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


# ------------------------------------------------------------------ incremental collection

def _cursor() -> dict:
    return _read_json(store.root() / ".stats" / "trace_cursor.json") or {}


def collect(*, hermes: bool = True, claude_code: bool = True, webui: bool = True, since_days: int | None = None,
            limit: int | None = None, should_stop=None) -> dict:
    """Build/refresh traces for everything new or changed since the last run. Incremental via a cursor
    of (path → size, mtime). should_stop() lets the caller abort (e.g. a worker request started)."""
    store.init()
    cur = _cursor()
    done = {"hermes": 0, "claude_code": 0, "webui": 0, "skipped": 0}
    covered = set()
    reqs = []
    if (HERMES / "state" / "requests").exists():
        for d in sorted((HERMES / "state" / "requests").iterdir()):
            r = _read_json(d / "request.json")
            if r:
                r["_dir"] = d
                reqs.append(r)
                sid = (r.get("executor") or {}).get("session_id")
                if sid:
                    covered.add(sid)
    cutoff = (_dt.datetime.now() - _dt.timedelta(days=since_days)).timestamp() if since_days else 0
    n = 0
    if hermes:
        for r in reqs:
            d = r["_dir"]
            key = str(d / "request.json")
            st = (d / "request.json").stat()
            stamp = [st.st_size, int(st.st_mtime)]
            if cur.get(key) == stamp or st.st_mtime < cutoff:
                done["skipped"] += 1
                continue
            if should_stop and should_stop():
                break
            t = build_hermes(d, reqs)
            if t:
                save(t)
                done["hermes"] += 1
                cur[key] = stamp  # only cursor finished requests; running ones are retried next time
            n += 1
            if limit and n >= limit:
                break
    if claude_code and CLAUDE_PROJECTS.exists():
        for f in sorted(CLAUDE_PROJECTS.glob("*/*.jsonl")):
            if f.stem in covered or "-hermes-state-requests-" in f.parent.name:
                continue
            st = f.stat()
            stamp = [st.st_size, int(st.st_mtime)]
            if cur.get(str(f)) == stamp or st.st_mtime < cutoff:
                done["skipped"] += 1
                continue
            if should_stop and should_stop():
                break
            t = build_claude_code(f)
            if t:
                save(t)
                done["claude_code"] += 1
            cur[str(f)] = stamp
            n += 1
            if limit and n >= limit:
                break
    if webui:
        req_projects: dict[str, set] = {}
        for r in reqs:
            if r.get("session_id"):
                req_projects.setdefault(str(r["session_id"]), set()).add(
                    r.get("project_id") or (project_name(r.get("project")) if r.get("project") else None))
        for f in webui_session_files():
            try:
                st = f.stat()
            except OSError:
                continue
            stamp = [st.st_size, int(st.st_mtime)]
            if cur.get(str(f)) == stamp or st.st_mtime < cutoff:
                done["skipped"] += 1
                continue
            if should_stop and should_stop():
                break
            t = build_webui(f, req_projects)
            if t:
                save(t)
                done["webui"] += 1
            cur[str(f)] = stamp
            n += 1
            if limit and n >= limit:
                break
    store._atomic_write(store.root() / ".stats" / "trace_cursor.json", json.dumps(cur))
    store.append_event("traces_collected", **done)
    return done


def attribute(t: dict, claim: bool = True) -> dict:
    """Attach a trace to its Project. claim=True (new traces, or the user creating a project) may set
    the project of a trace no Project owns yet; it never moves a trace between projects. Also records
    which linked folders the run touched (multi-repo analysis)."""
    from . import projects
    paths = [*(t.get("files_modified") or []), *(t.get("files_read") or [])]
    pid = projects.owner_of_key(str(t.get("project") or ""))
    if not pid and claim:
        pid = projects.project_for_path(t.get("cwd"))
        if not pid:
            owners = {projects.project_for_path(f) for f in (t.get("files_modified") or [])} - {None}
            pid = owners.pop() if len(owners) == 1 else None
        if pid:
            t["project"] = pid
    if pid:
        p = projects.get(pid) or {}
        touched = []
        for f in p.get("folders", []):
            fp = f["path"]
            hit = any(x == fp or str(x).startswith(fp + os.sep) for x in [t.get("cwd") or "", *paths])
            hit = hit or any(fp in c or tilde_in(c, fp) for c in (t.get("commands") or [])[:200])
            if hit:
                touched.append(f["label"])
        t["folders"] = touched
        t["project_id"] = pid
    return t


def tilde_in(cmd: str, path: str) -> bool:
    h = os.path.expanduser("~")
    return path.startswith(h) and ("~" + path[len(h):]) in cmd


def load_all(since: str | None = None) -> list[dict]:
    out = []
    base = store.root() / "sessions"
    for p in sorted(base.glob("*/*/*/trace.json")):
        t = _read_json(p)
        if t and (not since or (t.get("ended") or "") >= since):
            out.append(t)
    for t in out:
        attribute(t, claim=False)
    labels = _read_jsonl(store.root() / ".stats" / "labels.jsonl")
    for t in out:  # human labels beat heuristics
        lab = labels.get(t["id"])
        if lab:
            t["outcome"] = {"label": lab, "confidence": 1.0, "signals": ["human label"], "source": "human"}
    return out


def _read_jsonl(p: Path) -> dict:
    out = {}
    try:
        for ln in p.read_text().splitlines():
            try:
                x = json.loads(ln)
                out[x["trace"]] = x["label"]
            except (ValueError, KeyError):
                pass
    except OSError:
        pass
    return out


def label(trace_id: str, value: str) -> None:
    if value not in ("good", "bad"):
        raise ValueError("label is good or bad")
    d = store.root() / ".stats"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "labels.jsonl", "a") as f:
        f.write(json.dumps({"trace": trace_id, "label": "success" if value == "good" else "failure",
                            "at": _iso(_dt.datetime.now().timestamp())}) + "\n")
