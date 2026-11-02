"""Command Centre — Hermes plugin.

pre_llm_call hook  : classifies every user turn, creates/advances request state, ingests
                     attachments, and injects a routing directive for the hub model.
tools              : cc_claude_execute, cc_astra_plan, cc_execute_approved, cc_set_project, cc_status
slash command      : /cc  (status | project <name|path> | projects | cancel | planner | route <text>)

The hub model only dispatches. Prompts, attachments and approval state are read by the tools
from disk, so nothing is lost or paraphrased between hops. See ~/hermes/README.md.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from pathlib import Path

from . import executor_choice, limits_gate, projects, providers, questions, worker_profile
from .agents import AgentRun, strip_attach
from .attachments import Manifest, find_candidates, kind_of
from .common import REQUESTS_DIR, STATE_DIR, load_config, log_decision, read_json, write_json
from .providers import (WORKER_RULES, claude_transcript_tail, codex_models, git_state, run_astra_executor, run_claude,
                        run_planner, usage_limit_info, worker_rules)
from .common import LOCKS_DIR, agents
from .router import ASTRA, CLAUDE, classify, hub_tool_request, interpret_reply, looks_like_chat
from .state import Request, active_planning_request, session_state, save_session_state, set_active

_CTX = None
_HMEM_HOME = str(Path(__file__).resolve().parents[2])  # ~/hermes: hmem (institutional memory) lives here
_CLAUDE_SESSIONS = STATE_DIR / "claude_sessions.json"
TAG = "[command-centre]"


# --------------------------------------------------------------------------- helpers

def _text_of(message) -> str:
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        return "\n".join(p.get("text", "") for p in message
                         if isinstance(p, dict) and p.get("type") in ("text", "input_text"))
    return ""


def _planner_label(cfg: dict) -> str:
    p = providers.planner_config(cfg)
    return f"{p.get('label') or p.get('model')} ({p.get('provider')}:{p.get('model')})"


def _notify(text: str) -> None:
    try:
        if _CTX is not None:
            _CTX.post_message(text)
    except Exception:
        pass


def _live_feed():
    """Return f(text) that streams text into the WebUI's live "thinking" panel for the running tool
    call, or None outside one. The agent's tool_progress_callback is reached through the per-call
    progress relay (tool.progress events are not rendered by the WebUI; reasoning.available is)."""
    try:
        from agent.tool_progress_relay import current_tool_progress
        rep = current_tool_progress()
        if rep is None or not rep.__closure__:
            return None
        cells = dict(zip(rep.__code__.co_freevars, (c.cell_contents for c in rep.__closure__)))
        cb, name = cells.get("callback"), cells.get("tool_name", "command_centre")
        if not callable(cb):
            return None
    except Exception:
        return None

    def feed(text: str) -> None:
        try:
            cb("reasoning.available", name, text.rstrip() + "\n\n", None)
        except Exception:
            pass
    return feed


def _session_cwd() -> str | None:
    """The session's workspace: Hermes' session cwd, else TERMINAL_CWD (WebUI sets it per run)."""
    cwd = None
    try:
        cwd = _CTX.session_cwd() if _CTX is not None else None
    except Exception:
        pass
    return cwd or os.environ.get("TERMINAL_CWD") or None


def _worker_label(session_id: str) -> str:
    """The conversation's executor as the route line names it, e.g. "Claude Opus 5.5 (low)". The WebUI
    shows this model on the reply (worker-label.js) instead of the hub model that relayed it."""
    ch = executor_choice.get_choice(session_id)
    who = f"{'Claude' if ch['provider'] == 'anthropic' else 'Codex'} {ch['name']}"
    return f"{who} ({ch['effort']})" if ch["effort"] else who


def _route_line(r: Request) -> str:
    who = _worker_label(r["session_id"])
    if r["route"] == CLAUDE:
        return f"Route: {who} — {r['route_reason']}"
    return f"Route: Codex → approval → {who} — {r['route_reason']}"


def _known_projects_hint() -> str:
    names = sorted(projects.known_projects())[:15]
    return ", ".join(names) or "(none discovered)"


# --------------------------------------------------------------------------- verbatim relay (P4)
# The worker's own reply reaches the user unchanged: the tool writes it to requests/<id>/reply-<tok>.md
# and hands the hub only a placeholder line; transform_llm_output (registered with hold_stream, so the
# placeholder never streams) swaps it for the file before the reply is shown and stored. Older Hermes
# without hold_stream, or executor.relay: hub, keeps the old behaviour (the hub rewrites the result).

_RELAY = {"on": False}
_REPLY_RX = re.compile(r"\[\[cc:reply (\d{8}-\d{6}-[0-9a-f]{6}):([0-9a-f]{6,16})\]\]")
_PENDING: dict[str, list[tuple[str, str]]] = {}   # session id -> replies the hub was handed this turn
_PENDING_LOCK = threading.Lock()


def _relay_on(cfg: dict | None = None) -> bool:
    if not _RELAY["on"]:
        return False
    try:
        cfg = cfg if cfg is not None else load_config()
    except Exception:
        cfg = {}
    return str((cfg.get("executor", {}) or {}).get("relay", "verbatim")).lower() != "hub"


def _note_lines(r: Request, since: float = 0.0) -> list[str]:
    """One Note line per hand-off the delegate server had to refuse (limit, busy) during this run."""
    out = []
    for ln in (r.dir / "notes.jsonl").read_text().splitlines() if (r.dir / "notes.jsonl").exists() else []:
        try:
            n = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if n.get("at", 0) >= since and n.get("kind") == "delegate_refused" and n.get("reason") in ("limit", "busy"):
            line = f"Note: hand-off skipped: {n.get('note')}"
            if line not in out:
                out.append(line)
    return out


def _relay(r: Request, out: dict, body: str, *, keep: tuple[str, ...] = ()) -> dict:
    """Swap the tool result's long text for a reply placeholder (see above). keep: extra keys the hub
    still needs (e.g. action for a clarify offer)."""
    if not _relay_on() or not (body or "").strip():
        return out
    tok = secrets.token_hex(4)
    r.write_text(f"reply-{tok}.md", body.strip() + "\n")
    with _PENDING_LOCK:
        _PENDING.setdefault(r["session_id"] or "", []).append((r.id, tok))
    slim = {"request_id": r.id, "ok": out.get("ok"), "route": out.get("route"), "reply": f"[[cc:reply {r.id}:{tok}]]",
            "note": "Reply with the `route` line (if given), a blank line, then the `reply` line EXACTLY as written, "
                    "alone on its line. The app replaces it with the worker's own reply. Write nothing else."}
    for k in ("awaiting_answer", "stopped", *keep):
        if k in out:
            slim[k] = out[k]
    return slim


def _reply_body(r: Request, res: dict, *, clean: str | None = None, started: float = 0.0) -> str:
    """What the user reads under the route line: the worker's own text, or a short failure."""
    if res.get("stopped_by_user"):
        body = "Stopped by you. The work done so far stays in the project."
    elif res.get("ok"):
        body = strip_attach((clean if clean is not None else (res.get("result") or "")).strip()) or "Done."
    else:
        text = strip_attach((res.get("result") or "").strip())
        body = text or f"Problem: {str(res.get('error') or 'the worker failed')[:600]}"
    lines = _note_lines(r, started)
    denials = res.get("permission_denials") or []
    if denials:
        names = sorted({str(d.get("tool_name") or d.get("tool") or "a tool") for d in denials if isinstance(d, dict)})
        lines.append(f"Note: {len(denials)} tool call(s) were blocked by permissions ({', '.join(names)[:120]}).")
    return body + ("\n\n" + "\n".join(lines) if lines else "")


def _on_transform_llm_output(response_text=None, session_id: str = "", **kwargs):
    """Replace [[cc:reply …]] placeholders with the saved worker reply. Never raises: a held transform that
    fails would withhold the reply."""
    try:
        if not isinstance(response_text, str):
            return None
        with _PENDING_LOCK:
            pending = _PENDING.pop(session_id or "", [])
        used: set[tuple[str, str]] = set()

        def sub(m):
            f = REQUESTS_DIR / m.group(1) / f"reply-{m.group(2)}.md"
            try:
                body = f.read_text().rstrip()
            except OSError:
                return m.group(0)
            used.add((m.group(1), m.group(2)))
            return body
        new = _REPLY_RX.sub(sub, response_text)
        for rid, tok in pending:  # the hub dropped the placeholder: the reply is still shown
            if (rid, tok) in used:
                continue
            try:
                new = new.rstrip() + "\n\n" + (REQUESTS_DIR / rid / f"reply-{tok}.md").read_text().rstrip()
            except OSError:
                pass
        return new if new != response_text else None
    except Exception:
        return None


# --------------------------------------------------------------------------- early start (P3)
# On the direct route the worker starts from the routing hook, so the hub's first LLM call (~5s) runs
# alongside it. cc_claude_execute then attaches to the running job (live feed + result) instead of
# starting a second one.

_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()


class _FeedHolder:
    """Live-feed lines from a job that started before its tool call: buffered until the tool attaches."""

    def __init__(self):
        self.lock = threading.Lock()
        self.buf: list[str] = []
        self.target = None

    def __call__(self, text: str) -> None:
        with self.lock:
            if self.target:
                try:
                    self.target(text)
                except Exception:
                    pass
            else:
                self.buf.append(text)

    def attach(self, feed) -> None:
        with self.lock:
            for t in self.buf:
                try:
                    feed(t)
                except Exception:
                    pass
            self.buf.clear()
            self.target = feed


def _early_start(r: Request, cfg: dict) -> None:
    if not (cfg.get("executor", {}) or {}).get("early_start", False):
        return
    with _JOBS_LOCK:
        if r.id in _JOBS:
            return
        job = {"done": threading.Event(), "result": None, "feed": _FeedHolder(), "session_id": r["session_id"],
               "started": time.time(), "attached": False}
        _JOBS[r.id] = job

    def go():
        try:
            job["result"] = _execute(Request(r.id), feed=job["feed"])
        except Exception as exc:
            log_decision(event="early_start_failed", request=r.id, error=repr(exc)[:300])
            job["result"] = {"request_id": r.id, "ok": False, "error": f"worker failed to start: {exc!r}"}
        finally:
            job["done"].set()
    threading.Thread(target=go, daemon=True, name=f"cc-early-{r.id}").start()
    log_decision(event="early_start", request=r.id)


def _attach_job(rid: str):
    """The early-started job's result, waiting for it with this tool call's live feed. None = no job."""
    with _JOBS_LOCK:
        job = _JOBS.get(rid)
        if job:
            job["attached"] = True
    if not job:
        return None
    feed = _live_feed()
    if feed:
        job["feed"].attach(feed)
    job["done"].wait()
    with _JOBS_LOCK:
        _JOBS.pop(rid, None)
    return job["result"]


def _on_agent_loop_stopped(session_key: str = "", **kwargs):
    """The hub turn was stopped before it reached the tool: stop that turn's early-started worker."""
    try:
        with _JOBS_LOCK:
            jobs = [(rid, j) for rid, j in _JOBS.items() if not j["attached"] and not j["done"].is_set()
                    and j["session_id"] and (j["session_id"] == session_key or j["session_id"] in str(session_key))]
        for rid, j in jobs:
            d = REQUESTS_DIR / rid
            try:
                (d / providers.STOP_MARKER).write_text(json.dumps({"at": time.time(), "by": "hub stop"}))
            except OSError:
                pass
            pid = (read_json(d / "live.json", {}) or {}).get("pid")
            if isinstance(pid, int) and pid > 1:
                import signal as _signal
                try:
                    os.killpg(pid, _signal.SIGTERM)
                except (ProcessLookupError, PermissionError, OSError):
                    pass
            log_decision(event="early_start_cancelled", request=rid)
    except Exception:
        pass


def _on_api_request(**kw):
    """P0 observer: the hub's own calls (size, tools, latency, usage) into decisions.jsonl."""
    try:
        log_decision(event="hub_api", model=kw.get("model"), call=kw.get("api_call_count"),
                     tools=kw.get("tool_count"), approx_in=kw.get("approx_input_tokens"),
                     chars=kw.get("request_char_count"), session=str(kw.get("session_id") or "")[:60])
    except Exception:
        pass


def _on_api_response(**kw):
    try:
        u = kw.get("usage") or {}
        log_decision(event="hub_api_done", model=kw.get("model"), call=kw.get("api_call_count"),
                     duration=round(float(kw.get("api_duration") or 0), 2),
                     first_chunk=round(float(kw["first_chunk_at"]) - float(kw["started_at"]), 2)
                     if kw.get("first_chunk_at") and kw.get("started_at") else None,
                     usage={k: u.get(k) for k in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                                                   "cached_tokens", "prompt_tokens", "completion_tokens") if k in u},
                     session=str(kw.get("session_id") or "")[:60])
    except Exception:
        pass


# --------------------------------------------------------------------------- limits gate

_WHO = {"claude": "Claude", "codex": "Codex"}
_OTHER_NAME = {"claude": "Claude", "codex": "GPT"}


def _auto_effort(r: Request) -> str:
    """Effort for a run whose chat is on Auto: what the user typed decides (see executor_choice.auto_effort)."""
    return executor_choice.auto_effort(_brief_text(r))


def _takeover_peer(r: Request) -> str | None:
    """The GPT equivalent of the Claude model a takeover continues from (Opus 5.5 -> GPT-6.1 Sol)."""
    ex = r.data.get("executor") or {}
    if not str(ex.get("provider") or "").startswith("claude"):
        return None
    m = ex.get("model") or executor_choice.DEFAULT["model"]
    return executor_choice.peer_model("anthropic", m, (load_config().get("dynamic_agents") or {}).get("models") or {},
                                      _codex_models_safe())["model"]


def _limit_block(r: Request, agent: str, model: str | None, *, offer: bool = True) -> dict | None:
    """Before spawning: None = go. Blocked: when the other provider is linked, enabled and itself below the
    limit, {"switch": <its same-tier model>} and the caller runs that instead (no question); else nothing
    starts and the hub says one line with the reset time."""
    try:
        g = limits_gate.check(agent, model, purpose="work")
    except Exception as exc:  # the gate itself must never stop work
        log_decision(event="limits_gate_error", request=r.id, error=repr(exc)[:200])
        return None
    r["limits"] = g.as_dict()
    if not g.blocked:
        r.save()
        return None
    other = limits_gate.other(agent)
    log_decision(event="limit_blocked", request=r.id, agent=agent, model=model, pct=g.pct, window=g.window)
    if offer and agents().has(other) and providers.linked(other):
        prov = "anthropic" if agent == "claude" else "openai"
        peer = executor_choice.peer_model(prov, model or (executor_choice.DEFAULT["model"] if agent == "claude"
                                                           else "gpt-6-astra"),
                                          (load_config().get("dynamic_agents") or {}).get("models") or {},
                                          _codex_models_safe())
        if not limits_gate.check(other, peer["model"], purpose="work").blocked:
            log_decision(event="limit_switch_auto", request=r.id, to=other, model=peer["model"])
            return {"switch": peer}
    r["status"] = "limit_blocked"
    r.save()
    return {"request_id": r.id, "ok": False, "usage_limit": g.message,
            "note": f"Nothing was started. Tell the user in one line: {g.message}."
                    + ("" if agents().has(other) else "")}


def _codex_models_safe() -> list[str]:
    try:
        return codex_models()
    except Exception:
        return []


def _delegate_env(r: Request, run: AgentRun, *, parent: str, cwd: str, add_dirs: list[str], model, effort,
                  profile: str = "lean") -> dict | None:
    """Delegate-server env when dynamic agents are allowed for this run, else None."""
    if parent == "claude" and profile != "lean":
        return None
    try:
        ok, why = providers.dynamic_gate()
    except Exception:
        ok, why = False, "gate error"
    if not ok:
        return None
    main = run.main() or {}
    return providers.delegate_env(request_dir=r.dir.resolve(), parent_agent=main.get("id") or "", parent=parent,
                                  cwd=cwd, add_dirs=add_dirs, model=model, effort=effort, session_id=r["session_id"])


# --------------------------------------------------------------------------- directives

_DISPLAY_RULE = ("If the tool result has `display`, output it EXACTLY as-is after the route line and a blank line (it may end "
                 "with a ```hermes-question block that the app renders as a question card): never answer, "
                 "summarise or reformat the questions yourself.")
# With the verbatim relay the tool hands back a `reply` placeholder line instead of the worker's text.
_REPLY_RULE = ("If the tool result has `reply`, write ONLY the route line, a blank line, and the `reply` line exactly as "
               "given (the app swaps it for the worker's own reply). If it has `action`, do that instead. Otherwise: ")

# Plugin tools sit behind Hermes' tool-search bridge; spelling out the call saves the model a
# tool_describe round trip (~5s) on every request.
_DIRECT = (" Its only parameter is request_id: if it isn't in your tool list, invoke it straight away "
           "with tool_call {\"calls\": [{\"name\": \"<tool>\", \"arguments\": {\"request_id\": \"<id>\"}}]} "
           "— never tool_search or tool_describe first.")


def _directive_execute(r: Request) -> str:
    return (
        f"{TAG} {_route_line(r)}\n"
        f"request_id={r.id} project={r['project'] or 'none'} attachments={len(Manifest(r.dir).items)}\n"
        f"ACTION: call cc_claude_execute(request_id=\"{r.id}\") now." + _DIRECT + f" Do not plan, rewrite the prompt, "
        f"ask questions, or do the work yourself — the tool sends the user's original words and "
        f"original attachments to Claude Code. {_REPLY_RULE}Reply starting with the line `{_route_line(r)}`, then "
        f"relay Claude's result as short bullets (Done / Verified / Note; failures as Problem / "
        f"Cause / Required action). No narration, no restating the request. {_DISPLAY_RULE}"
    )


def _directive_plan(r: Request, cfg: dict) -> str:
    return (
        f"{TAG} {_route_line(r)}\n"
        f"request_id={r.id} planner={_planner_label(cfg)} attachments={len(Manifest(r.dir).items)}\n"
        f"ACTION: call cc_astra_plan(request_id=\"{r.id}\") now." + _DIRECT + f" {_REPLY_RULE}Reply starting with the line "
        f"`{_route_line(r)}`, then show the tool's `display` text EXACTLY as-is (the planner's proposal, or a "
        f"```hermes-question block the app renders as a question card). Do NOT execute anything "
        f"and do NOT call cc_claude_execute — execution waits for the user's approval."
    )


def _directive_execute_approved(r: Request) -> str:
    return (
        f"{TAG} User approved request {r.id}"
        + (f" with modifications: {r['modifications'][-1]!r}" if r["modifications"] else "")
        + f".\nACTION: call cc_execute_approved(request_id=\"{r.id}\") now." + _DIRECT + f" {_REPLY_RULE}Reply starting with "
        f"`Route: Codex → approval → {_worker_label(r['session_id'])} — executing approved brief`, then the line "
        f"`[[plan:{r.id}]]` exactly as written, on its own (the app shows the planner's brief there, collapsed; "
        f"never paste the brief yourself), show any changed points the tool reports (one bullet each), then relay "
        f"Claude's result as short Done / Verified / Note bullets."
    )


def _directive_ask_project(r: Request) -> str:
    return (
        f"{TAG} {_route_line(r)} — but no project is selected, and this looks like project work.\n"
        f"ACTION: ask the user ONE short question: which project? Known: {_known_projects_hint()}. "
        f"They can answer with a name or path (or `/cc project <name>`). Do not do anything else."
    )


# --------------------------------------------------------------------------- the hook

def _tiebreak(text: str) -> bool:
    """Ask the hub model whether planning is genuinely valuable. Defaults to False (Claude)."""
    try:
        res = _CTX.llm.complete_structured(
            instructions=("Decide if this request needs a planning/approval phase before coding. "
                          "Say true ONLY for broad scope, product/UX/creative direction, architecture, "
                          "several deliverables, migrations or cross-system work. Small or clearly "
                          "specified tasks -> false. When unsure -> false."),
            input=[{"type": "text", "text": text[:4000]}],
            json_schema={"type": "object", "properties": {"plan": {"type": "boolean"}},
                         "required": ["plan"], "additionalProperties": False},
            schema_name="cc.route", purpose="command-centre.route", temperature=0.0, max_tokens=20,
        )
        return bool(res.parsed and res.parsed.get("plan"))
    except Exception:
        return False


def _mark_worker_turn(session_id: str, directive) -> None:
    """Tell fragment-memory (next pre_llm_call hook, same process) this turn goes to a worker: the hub
    only dispatches, so its memory recall (and the git work behind it) is skipped. The worker gets its
    own memory titles."""
    if isinstance(directive, str) and "ACTION: call cc_" in directive:
        try:
            ret = _hmem().retrieve
            marks = getattr(ret, "WORKER_TURNS", None)
            if marks is None:
                marks = {}
                setattr(ret, "WORKER_TURNS", marks)
            marks[session_id] = time.time()
        except Exception:
            pass


def _on_pre_llm_call(session_id: str = "", user_message=None, **kwargs):
    try:
        out = _route_turn(session_id or "default", user_message, kwargs.get("conversation_history"))
        _mark_worker_turn(session_id or "default", out)
        return out
    except Exception as exc:  # never break the chat; surface the failure instead
        log_decision(event="hook_error", error=repr(exc))
        return f"{TAG} routing error: {exc!r}. Tell the user routing failed and why; do not pretend it worked."


def _attached_text(user_message, limit: int = 20000) -> str:
    """Text/markdown files sent with the message (a brief pasted as a file): read for routing, effort and
    memory lookup, which would otherwise only see "I've uploaded 2 file(s)"."""
    out, left = [], limit
    try:
        paths, _ = find_candidates(user_message)
    except Exception:
        return ""
    for p in paths[:5]:
        try:
            if kind_of(p.suffix, None) != "text" or not p.is_file() or p.stat().st_size > 2 * limit:
                continue
            t = p.read_text(encoding="utf-8", errors="replace")[:left]
        except OSError:
            continue
        out.append(t)
        left -= len(t)
        if left <= 0:
            break
    return "\n\n".join(out)


def _brief_text(r: Request) -> str:
    """What the user asked, including a brief attached as a text file."""
    return r.data.get("effective_text") or r.data.get("raw_message") or r["original_prompt"] or ""


_UNFINISHED_RX = re.compile(r"\b(i'?ll|i will|will) (check|pull|report|let you know|look|review|update you)\b.{0,80}"
                            r"\b(once|when|after)\b|\bonce (they|it|the \w+) (report|finish|complete|are done|is done)"
                            r"|\bstill (running|working)\b", re.I | re.S)
_UNFINISHED_NUDGE = ("Your run ended while work you started in the background was still running, so the user got "
                     "no result. Wait for every background subagent and command to finish, check their results, "
                     "then write the final message with the actual outcome.")


def _left_unfinished(result: str, run: AgentRun) -> bool:
    """A reply that promises to report back later, from a run that started background work."""
    bg = any(a.get("kind") == "background" or a.get("async") for a in run.agents)
    return bg and bool(_UNFINISHED_RX.search(result[-600:]))


def _cwd(r: Request) -> str:
    """Where the worker runs: the project, else this chat's own folder (kept across its requests, so a
    follow-up like "make it blue" finds the previous work and can resume the same worker session)."""
    if r["project"]:
        return r["project"]
    sid = re.sub(r"[^A-Za-z0-9_-]", "_", str(r["session_id"] or ""))[:80]
    return str((STATE_DIR / "chat-work" / sid).resolve()) if sid else str((r.dir / "work").resolve())


def _resume_key(r: Request) -> str:
    """Worker sessions resume per chat (and project): another chat's history never leaks in."""
    return f"{r['session_id']}:{r.data.get('project_id') or r['project'] or '-'}"


def _recent_block(r: Request, turns: int = 6, cap: int = 600) -> str:
    """The chat's last turns, for a worker that starts fresh (no session to resume)."""
    recent = [m for m in (session_state(r["session_id"]).get("recent") or []) if m.get("content")]
    cur = (r.data.get("raw_message") or "").strip()
    if recent and recent[-1].get("role") == "user" and recent[-1]["content"].strip() == cur:
        recent = recent[:-1]
    recent = recent[-turns:]
    if not recent:
        return ""
    lines = [f"{m['role'].upper()}: {' '.join(m['content'].split())[:cap]}" for m in recent]
    return ("RECENT CONVERSATION IN THIS CHAT (oldest first; resolve 'it', 'that', 'the site' from here):\n"
            + "\n".join(lines))


def _remember(session_id: str, history) -> None:
    """Keep a compact rolling copy of the conversation for takeover handoffs."""
    recent = []
    for m in (history or [])[-12:]:
        if isinstance(m, dict) and m.get("role") in ("user", "assistant"):
            c = m.get("content")
            c = c if isinstance(c, str) else _text_of(c)
            if c and not c.startswith(TAG):
                recent.append({"role": m["role"], "content": c[:1200]})
    st = session_state(session_id)
    st["recent"] = recent
    save_session_state(session_id, st)


def _route_turn(session_id: str, user_message, history=None):
    text = _text_of(user_message).strip()
    try:
        _remember(session_id, history)
    except Exception:
        pass
    write_json(STATE_DIR / "last_session.json", {"session_id": session_id, "at": time.time()})
    st0 = session_state(session_id)
    if text.startswith(f"{TAG}-resume") or (st0.get("takeover") and text.lower() in ("continue", "go", "resume")):
        limited = [d for d in (read_json(f, {}) for f in sorted(REQUESTS_DIR.glob("*/request.json"))[-20:])
                   if d and d.get("session_id") == session_id and d.get("status") == "claude_limited"]
        if limited:
            rid = limited[-1]["id"]
            return (f"{TAG} Route: Codex (takeover) — resuming {rid}.\nACTION: call "
                    f"cc_takeover(request_id=\"{rid}\", accept=true) now and relay the result as short bullets.")
    if st0.get("takeover_offer"):
        kind, _ = interpret_reply(text)
        no = text.strip().lower().rstrip(".!") in ("no", "nope", "no thanks", "nah", "decline")
        if kind in ("approve", "approve_with_changes") or no:
            offer = st0["takeover_offer"]
            rid = offer.get("rid") if isinstance(offer, dict) else offer
            return (f"{TAG} User answered the takeover offer for {rid}: {'NO' if no else 'YES'}.\nACTION: call "
                    f"cc_takeover(request_id=\"{rid}\", accept={'false' if no else 'true'}) now.")
    if text.lower().startswith("/motion"):
        return _route_motion(session_id, user_message, text)
    if not text or text.startswith("/") or text.startswith(TAG):
        return None
    cfg = load_config()
    st = session_state(session_id)
    overridden = classify(text, cfg).forced

    # 0) a worker (Claude / Astra executor) asked the user a question: this message is the answer
    answered = _route_answer(session_id, st, text, overridden)
    if answered:
        return answered

    # 1) waiting for a project choice
    pending_proj = st.get("needs_project")
    if pending_proj and not overridden:
        path, msg = projects.set_current(text.split()[0] if len(text.split()) <= 3 else text)
        r = Request(pending_proj)
        st["needs_project"] = None
        save_session_state(session_id, st)
        if path and r.exists:
            r["project"], r["extra_dirs"] = projects.folders_for(path)
            r["project_source"] = "user choice"
            r.save()
            if r["route"] == CLAUDE:
                _early_start(r, cfg)
                return _directive_execute(r)
            return _directive_plan(r, cfg)
        # not a project answer: fall through and treat as a fresh request

    # 2) an open planning conversation
    active = active_planning_request(session_id)
    if active and not overridden:
        Manifest(active.dir).ingest(user_message, cfg.get("attachments", {}) or {})
        status = active["status"]
        if status == "awaiting_approval":
            kind, mod = interpret_reply(text)
            if kind == "cancel":
                active["status"] = "cancelled"
                active.save()
                set_active(session_id, None)
                log_decision(event="cancelled", request=active.id)
                return f"{TAG} Request {active.id} cancelled by the user. Confirm briefly; do nothing else."
            if kind in ("approve", "approve_with_changes"):
                active["planning_transcript"].append({"role": "user", "content": text})
                if mod:
                    active["modifications"].append(mod)
                active["status"] = "approved"
                active["approved_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                active.save()
                log_decision(event="approved", request=active.id, modification=mod or None)
                return _directive_execute_approved(active)
        if status == "approved":
            return _directive_execute_approved(active)
        # answers to questions, or a revision request
        active["pending_reply"] = text
        active["status"] = "planning"
        active.save()
        return _directive_plan(active, cfg)

    # 3) the user's own board, todos or scheduled tasks: the hub's tools, not a worker's
    kind = None if overridden else hub_tool_request(text)
    if kind:
        log_decision(event="routed", route="hermes", reason=f"hub tools ({kind})")
        tools = ("the workboard_* tools (kanban cards and todos, shown in the Kanban and Todo panels)" if kind == "board"
                 else "the cronjob_manage tool (scheduled tasks, shown in the Tasks panel)")
        return (f"{TAG} Route: Tabbie — your {'board and todos' if kind == 'board' else 'scheduled tasks'}. Do this "
                f"YOURSELF with {tools}; do not call cc_* tools. Start the reply with that route line, then "
                f"confirm what changed in one or two short lines.")

    # 4) a new request
    decision = classify(text, cfg)
    attached = _attached_text(user_message)
    effective = (text + "\n\n" + attached).strip() if attached else ""
    if effective and not decision.forced:  # a brief sent as a file decides the route too
        d2 = classify(effective, cfg)
        decision.route, decision.reason, decision.score = d2.route, d2.reason, d2.score
    if (not decision.forced and decision.route == CLAUDE and cfg.get("router", {}).get("llm_tiebreak")
            and decision.score == int(cfg.get("router", {}).get("plan_threshold", 3)) - 1
            and _tiebreak(decision.text)):
        decision.route, decision.reason = ASTRA, "planning judged valuable (tie-break)"
    registry_project = projects.registry_for(decision.text, _session_cwd())
    if registry_project:
        project, source = projects.registry_folders(registry_project)[0], f"project {registry_project['name']}"
    else:
        project, source = projects.resolve(decision.text, _session_cwd())
    # Claude executes everything by default; the hub answers by itself only when asked
    # ("@hermes …") or when router.hub_answers_chat is switched on for chit-chat.
    hub_words = [w.lower() for w in (cfg.get("router", {}).get("overrides", {}) or {}).get("hermes", [])]
    hub_forced = next((w for w in hub_words if text.strip().lower().startswith(w)), None)
    if hub_forced or (cfg.get("router", {}).get("hub_answers_chat", False)
                      and project is None and decision.route == CLAUDE and not decision.forced
                      and looks_like_chat(decision.text)):
        log_decision(event="routed", route="hermes", reason="general chat", project_source=source)
        return (f"{TAG} Route: Tabbie — general chat (no project targeted). Answer directly and "
                f"concisely yourself; do not call cc_* tools. If the user actually wants project work, "
                f"tell them to pick a workspace or name the project.")
    if registry_project:  # a Project's own folders only (never extra WebUI spaces)
        project, extra_dirs = projects.registry_folders(registry_project)
    else:
        project, extra_dirs = projects.folders_for(project)
        project, extra_dirs = projects.expand_with_project(session_id, project, extra_dirs)
    r = Request.create(session_id=session_id, route=decision.route, reason=decision.reason,
                       forced=decision.forced, prompt=decision.text, raw_message=text,
                       project=project, project_source=source)
    if effective:
        r["effective_text"] = effective[:20000]
    if registry_project:
        r["project_id"] = registry_project["id"]
    if extra_dirs:
        r["extra_dirs"] = extra_dirs
    if registry_project or extra_dirs:
        r.save()
    added = Manifest(r.dir).ingest(user_message, cfg.get("attachments", {}) or {})
    log_decision(event="routed", request=r.id, route=decision.route, reason=decision.reason,
                 score=decision.score, forced=decision.forced, project=project,
                 project_source=source, attachments=len(added))
    if decision.route == ASTRA:
        set_active(session_id, r.id)
        return _directive_plan(r, cfg)
    if project is None and source != "general" and not registry_project:
        st = session_state(session_id)
        st["needs_project"] = r.id
        save_session_state(session_id, st)
        return _directive_ask_project(r)
    _early_start(r, cfg)
    return _directive_execute(r)


def _route_answer(session_id: str, st: dict, text: str, overridden: bool):
    rid = st.get("awaiting_question")
    if not rid:
        return None
    r = Request(rid)
    st["awaiting_question"] = None
    save_session_state(session_id, st)
    if not r.exists or r["status"] != "awaiting_answer":
        return None
    if overridden or text.strip().lower().rstrip(".!") in ("cancel", "never mind", "nevermind", "stop"):
        r["status"] = "unanswered"
        r.save()
        log_decision(event="question_dropped", request=r.id)
        if overridden:
            return None  # a new @claude/@astra request: route it normally
        return f"{TAG} Request {r.id}: the user dropped the question. Confirm in one line; do nothing else."
    q = r.data.get("question") or {}
    q["answer"], q["answered_at"] = text, time.strftime("%Y-%m-%dT%H:%M:%S")
    r["question"] = q
    r.data.setdefault("question_log", []).append(q)
    r["pending_answer"] = text
    r["status"] = "answered"
    r.save()
    log_decision(event="question_answered", request=r.id, source=q.get("source"))
    _early_start(r, load_config())
    who = "Codex" if q.get("source") == "astra" else "Claude"
    return (f"{TAG} The user answered {who}'s question for request {r.id}.\nACTION: call "
            f"cc_claude_execute(request_id=\"{r.id}\") now;" + _DIRECT + f" it resumes the same {who} session with the answers. "
            f"{_REPLY_RULE}Reply starting with `Route: {who} — resumed with your answers`, then relay the result as short "
            f"Done / Verified / Note bullets. {_DISPLAY_RULE}")


def _ask_user(r: Request, source: str, data: dict, clean: str, res: dict, run: AgentRun) -> dict:
    """Park the request: the worker asked structured questions. The next user message answers them."""
    r["status"] = "awaiting_answer"
    # A question that was never answered through the card (e.g. a steer resumed the worker) still
    # counts as a round, so each ask gets its own round number.
    prev, log = r.data.get("question") or {}, r.data.setdefault("question_log", [])
    if prev.get("round") and not any(x.get("round") == prev["round"] for x in log):
        log.append({**prev, "answer": None, "superseded": True})
    rnd = len(log) + 1
    r["question"] = {"source": source, "questions": data["questions"], "round": rnd,
                     "asked_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    r.save()
    run.finish_main(True, waiting=True)
    run.save(force=True)
    st = session_state(r["session_id"])
    st["awaiting_question"] = r.id
    save_session_state(r["session_id"], st)
    log_decision(event="question_asked", request=r.id, source=source, n=len(data["questions"]))
    who = "Codex" if source == "astra" else "Claude"
    display = ((clean + "\n\n") if clean else "") + questions.block(data, request_id=r.id, source=source, round=rnd)
    out = {"request_id": r.id, "ok": True, "awaiting_answer": True,
           "duration_s": res.get("duration_s"),
           "display": display,
           "plain_questions": questions.as_text(data),
           "note": f"{who} needs the user's decision before continuing. Output `display` exactly; the user's "
                   f"next message is the answer and resumes the same {who} session."}
    return _relay(r, out, display)


def _agent_events(run: AgentRun, kind: str):
    def on_event(ev):
        with run.lock:
            if (run.apply_claude(ev) if kind == "claude" else run.apply_codex(ev)):
                run.save()
    return on_event


def _route_motion(session_id: str, user_message, text: str):
    """/motion <brief>: Claude Code in the Motion studio, current workspace = product source (read-only).
    Motion not installed yet (Node.js, studio folder, packages): starts that install instead and says so."""
    _hmem()  # puts hmem on sys.path
    from hmem import motion as motion_studio
    cfg = load_config()
    studio = motion_studio.studio_dir()
    if not motion_studio.installed(studio):
        st = motion_studio.install()
        log_decision(event="motion_install", state=st["state"], step=st["step"])
        if st["state"] == "failed":
            what = f"The Motion install failed: {st['error']} Tell the user, and that they can retry with /motion again or the Download button in the Motion panel."
        else:
            step = {"node": "installing Node.js", "studio": "creating the studio folder",
                    "packages": "installing the studio's Remotion packages"}.get(st["step"], "starting")
            what = (f"Motion isn't installed on this Mac yet, so Tabbie has just started installing it in the background "
                    f"(now: {step}). It downloads Node.js and the studio's packages, about 300 MB, a few minutes. Tell the "
                    "user that in 2-3 short sentences: they can watch it in the Motion panel (rail, under Chat), and "
                    "should send their /motion request again once it's done. Their brief was not started.")
        return f"{TAG} {what} Do not call any tools and do not try to install anything yourself."
    brief = text[len("/motion"):].strip()
    project, source = projects.resolve(brief, _session_cwd())
    primary, extras = projects.folders_for(project)
    primary, extras = projects.expand_with_project(session_id, primary, extras)
    product = [f for f in [primary, *extras] if f and Path(f).resolve() != studio.resolve()]
    skill = Path.home() / ".claude" / "skills" / "motion" / "SKILL.md"
    how = (f"Use the `motion` skill ({skill}). " if skill.exists() else
           f"Follow the studio's CLAUDE.md ({studio / 'CLAUDE.md'}). ")
    prompt = (f"Motion studio: {studio} (your working folder). {how}Style: {studio / 'MOTION_RULES.md'}. "
              "Do not use Higgsfield.\n"
              f"BRIEF: {brief or '(none given - ask once what the film should do)'}\n"
              f"PRODUCT SOURCE (read-only): {', '.join(product) if product else '(none selected - ask which app)'}")
    r = Request.create(session_id=session_id, route=CLAUDE, reason="motion studio (/motion)", forced=True,
                       prompt=prompt, raw_message=text, project=str(studio), project_source="/motion")
    r["extra_dirs"] = product
    r.save()
    Manifest(r.dir).ingest(user_message, cfg.get("attachments", {}) or {})
    log_decision(event="routed", request=r.id, route="claude", reason="/motion", product=product, source=source)
    _early_start(r, cfg)
    return _directive_execute(r)


# --------------------------------------------------------------------------- tools

def _claude_prompt(r: Request, manifest: Manifest, history: bool = False) -> str:
    parts = [f"COMMAND CENTRE REQUEST {r.id}", _route_line(r), _project_lines(r),
             "", "ORIGINAL USER REQUEST (verbatim):", r["original_prompt"]]
    if r["route"] == ASTRA:
        replies = [t["content"] for t in r["planning_transcript"] if t["role"] == "user"]
        if replies:
            parts += ["", "USER REPLIES DURING PLANNING (verbatim, in order):"] + [f"- {x}" for x in replies]
        parts += ["", f"APPROVED EXECUTION BRIEF (planner: {r['planner']}; approved by the user "
                      f"{r.data.get('approved_at')}). Follow it; the original request and attachments "
                      f"above/below remain the source of truth if anything is ambiguous:",
                  r["brief"]]
    recent = _recent_block(r) if history else ""
    if recent:
        parts += ["", recent]
    parts += ["", manifest.render(), "", _memory_context(r)]
    parts += ["", f"IMAGES FOR THE USER: save screenshots/renders to show under {(r.dir / 'attach').resolve()} "
                  "(or inside the project folders above), then write `ATTACH: <absolute path>` on its own line. "
                  "Images anywhere else are not shown."]
    return "\n".join(parts)


def _project_lines(r: Request) -> str:
    """The project header of a worker prompt: a Hermes Project's identity + folders, else the folder(s)."""
    pid = r.data.get("project_id")
    if pid:
        try:
            p = _hmem().projects.get(pid)
            if p:
                return f"WORKING DIRECTORY (primary folder): {r['project']}\n" + _hmem().projects.context_block(
                    p, r.data.get("raw_message") or r["original_prompt"] or "")
        except Exception as exc:
            log_decision(event="project_context_failed", request=r.id, error=str(exc)[:200])
    if not r["project"]:
        return (f"WORKING DIRECTORY: {_cwd(r)} (this chat's own folder; no project selected. Files you make "
                "for the user go here unless they name another place)")
    return (f"WORKING DIRECTORY / PROJECT: {r['project']}"
            + (f"\nALSO PART OF THIS WORKSPACE (you may read/edit): {', '.join(r.data.get('extra_dirs') or [])}"
               if r.data.get("extra_dirs") else ""))


def _hmem():
    import sys
    if _HMEM_HOME not in sys.path:
        sys.path.insert(0, _HMEM_HOME)
    import hmem.extract
    import hmem.projects
    import hmem.retrieve
    import hmem.trace
    return hmem


def _memory_context(r: Request) -> str:
    """Progressive disclosure: the memory map + a few relevant titles for this project (never bodies)."""
    try:
        h = _hmem()
        project = r.data.get("project_id") or (h.trace.project_name(r["project"]) if r["project"] else None)
        return h.retrieve.worker_context(_brief_text(r)[:2000], project, request_id=r.id)
    except Exception as exc:  # memory must never block a run
        log_decision(event="memory_context_failed", request=r.id, error=str(exc)[:200])
        return "MEMORY: ~/hermes/memory/README.md explains what exists and how to search it."


def _learn(r: Request) -> None:
    """After a run: sanitised trace + lesson candidates (+ maybe a background dream), off the request's path."""
    import threading

    def go():
        try:
            _hmem().extract.after_request(r.dir)
        except Exception as exc:
            log_decision(event="trace_failed", request=r.id, error=str(exc)[:200])
    threading.Thread(target=go, daemon=True, name=f"hmem-trace-{r.id}").start()


def _execute(r: Request, feed=None, force: dict | None = None) -> dict:
    """_run_request, but an unexpected error ends the request as failed instead of leaving it "executing"
    (shown as running and blocking updates until the reaper finds it)."""
    try:
        return _run_request(r, feed=feed, force=force)
    except Exception as exc:
        log_decision(event="execute_crashed", request=r.id, error=repr(exc)[:300])
        try:
            r = Request(r.id)
            if r["status"] in ("executing", "planning", "approved", "answered"):
                r["status"] = "failed"
                r["result"] = {"ok": False, "error": f"Tabbie hit an internal error: {exc!r}"[:300]}
                r.save()
            run = AgentRun.load(r.id, r["session_id"])
            with run.lock:
                run.finish_main(False, step="Tabbie hit an internal error")
                run.save(force=True)
        except Exception:
            pass
        return {"request_id": r.id, "ok": False, "error": f"Tabbie hit an internal error: {exc!r}"[:300],
                "note": "Tell the user in one line that the run failed with an internal error and they can send it again."}


def _run_request(r: Request, feed=None, force: dict | None = None) -> dict:
    """Run the request on the conversation's executor. feed: live-feed sink (early start), else this tool
    call's. force: {provider: anthropic|openai, model} to run this one request elsewhere (the user accepted
    a limit switch)."""
    cfg = load_config()
    manifest = Manifest(r.dir)
    cwd = _cwd(r)
    Path(cwd).mkdir(parents=True, exist_ok=True)
    answer = r.data.get("pending_answer")
    q = r.data.get("question") or {}
    choice = executor_choice.get_choice(r["session_id"])
    if force:
        info = executor_choice.model_info(force["provider"], force["model"]) or {"name": force["model"]}
        choice = {**choice, "provider": force["provider"], "model": force["model"], "name": info["name"],
                  "worker": executor_choice.CATALOG[force["provider"]]["worker"],
                  "effort": force.get("effort") or choice["effort"]}
    picked = choice if choice["provider"] == "openai" else None
    if answer and q.get("source") == "astra":
        chosen_run = (r.data.get("executor") or {}).get("chosen")
        return _astra_takeover(r, reason="resumed with your answers", choice=picked if chosen_run else None, feed=feed)
    if session_state(r["session_id"]).get("takeover") and not answer and agents().has("codex") and not force:
        return _astra_takeover(r, reason="takeover mode is on for this session", feed=feed)
    if picked and not answer:  # the user picked an OpenAI model for this conversation: Codex executes
        return _astra_takeover(r, reason=r["route_reason"], choice=picked, feed=feed, offer=not force)
    claude_model = choice["model"] if choice["provider"] == "anthropic" else None
    claude_effort = (choice["effort"] if choice["provider"] == "anthropic" else None) or _auto_effort(r)
    ecfg = dict(cfg.get("executor", {}) or {})
    blocked = _limit_block(r, "claude", claude_model or ecfg.get("model"), offer=not force)
    if blocked and blocked.get("switch"):  # Claude is at its limit: its GPT equivalent does this one
        peer = blocked["switch"]
        return _astra_takeover(r, reason=f"Claude {choice['name']} is at its limit, so its equivalent took this one",
                               choice={"provider": "openai", "model": peer["model"], "name": peer["name"],
                                       "effort": claude_effort}, feed=feed, offer=False)
    if blocked:
        return blocked
    resume = None
    sessions = read_json(_CLAUDE_SESSIONS, {}) or {}
    if answer:  # the worker asked; continue exactly that Claude session with the answers
        resume = (r.data.get("executor") or {}).get("session_id")
        prompt = questions.answer_prompt(q, answer)
        r.write_text(f"answer-{len(r.data.get('question_log') or [])}.md", prompt)
    else:
        if r["route"] == CLAUDE and ecfg.get("resume_project_session", True):
            prev = sessions.get(_resume_key(r))
            if prev and time.time() - prev.get("at", 0) < 60 * int(ecfg.get("resume_max_age_minutes", 120)):
                resume = prev.get("session_id")
        prompt = _claude_prompt(r, manifest, history=not resume)
        r.write_text("claude_prompt.md", prompt)
    r["status"] = "executing"
    r.save()
    feed = feed or _live_feed()
    running = [d for d in (read_json(f, {}) for f in sorted(REQUESTS_DIR.glob("*/request.json"))[-60:])
               if d and d.get("status") == "executing" and d.get("id") != r.id]
    # Each conversation has its own queue; only wait behind this conversation's job (or a full machine).
    busy = [d for d in running if d.get("session_id") == r["session_id"]]
    if not busy and len(running) >= int(ecfg.get("max_parallel", 1)):
        busy = running
    if busy:
        msg = (f"Queued: Claude is still working on {busy[-1]['id']} "
               f"({(busy[-1].get('raw_message') or '')[:60]}). Starts when that finishes.")
        _notify(msg)
        (feed or (lambda _t: None))(msg)
    _notify(f"Using Claude Code: {'continuing session in ' if resume else ''}{cwd}")
    add_dirs = ([str(manifest.dir)] if manifest.items else []) + list(r.data.get("extra_dirs") or [])
    run = AgentRun.load(r.id, r["session_id"])
    run.attach_roots = [cwd, *add_dirs, str(r.dir)]  # where an ATTACH: <image> line may point
    run.start_main("Claude Code" + (" · answered" if answer else ""), "claude",
                   description="resumed with your answers" if answer else "main worker")
    run.save(force=True)
    profile = worker_profile.profile_for(ecfg, r.data.get("raw_message") or r["original_prompt"] or "")
    ecfg["_dynamic_hints"] = (cfg.get("dynamic_agents", {}) or {}).get("hints") or []
    cfg = {**cfg, "executor": ecfg}
    delegate = _delegate_env(r, run, parent="claude", cwd=cwd, add_dirs=add_dirs, model=claude_model,
                             effort=claude_effort, profile=profile)
    timings: dict = {}
    started = time.time()
    res = run_claude(prompt, cwd=cwd, add_dirs=add_dirs, cfg=cfg, resume_session=resume, on_progress=feed,
                     session_key=r["session_id"], on_event=_agent_events(run, "claude"),
                     model=claude_model, effort=claude_effort, inbox=r.dir, profile=profile, delegate=delegate,
                     timings=timings)
    if not res.get("ok") and resume and not answer and "session" in str(res.get("error", "")).lower():
        sent = res.get("live_messages") or []
        res = run_claude(prompt, cwd=cwd, add_dirs=add_dirs, cfg=cfg, resume_session=None, on_progress=feed,
                         session_key=r["session_id"], on_event=_agent_events(run, "claude"),
                         model=claude_model, effort=claude_effort, inbox=r.dir, profile=profile, delegate=delegate,
                         timings=timings)
        res["live_messages"] = sent + (res.get("live_messages") or [])
    if res.get("ok") and res.get("session_id") and _left_unfinished(res.get("result") or "", run):
        # it ended its reply while background work it started was still going: finish it now, once
        log_decision(event="unfinished_resumed", request=r.id)
        (feed or (lambda _t: None))("Finishing the background work it left running…")
        sent = res.get("live_messages") or []
        with run.lock:
            run.main()["step"] = "Finishing background work"
        more = run_claude(_UNFINISHED_NUDGE, cwd=cwd, add_dirs=add_dirs, cfg=cfg, resume_session=res["session_id"],
                          on_progress=feed, session_key=r["session_id"], on_event=_agent_events(run, "claude"),
                          model=claude_model, effort=claude_effort, inbox=r.dir, profile=profile, delegate=delegate,
                          timings=timings)
        if more.get("ok") and (more.get("result") or "").strip():
            res = {**more, "live_messages": sent + (more.get("live_messages") or [])}
    r["pending_answer"] = None
    r["executor"] = {"provider": "claude-cli", "billing": res.get("billing"), "model": claude_model,
                     "effort": claude_effort, "auto_effort": not choice["effort"],
                     "session_id": res.get("session_id") or resume, "resumed": bool(resume),
                     "profile": res.get("profile") or profile, "dynamic": bool(delegate)}
    r["timings"] = {**(r.data.get("timings") or {}), **{k: round(v, 3) for k, v in timings.items()}}
    r["result"] = res
    r["status"] = "done" if res.get("ok") else ("stopped" if res.get("stopped_by_user") else "failed")
    r.save()
    clean, asked = questions.extract(res.get("result") or "") if res.get("ok") else ("", None)
    with run.lock:
        run.finish_main(bool(res.get("ok")), step=None if res.get("ok") else res.get("error"),
                        stopped=bool(res.get("stopped_by_user")))
        run.save(force=True)
    if res.get("session_id"):
        sessions = read_json(_CLAUDE_SESSIONS, {}) or {}  # fresh: other chats may have saved meanwhile
        sessions[_resume_key(r)] = {"session_id": res["session_id"], "at": time.time()}
        cutoff = time.time() - 7 * 86400
        sessions = {k: v for k, v in sessions.items() if isinstance(v, dict) and v.get("at", 0) > cutoff}
        write_json(_CLAUDE_SESSIONS, sessions)
    log_decision(event="executed", request=r.id, ok=res.get("ok"), billing=res.get("billing"),
                 duration_s=res.get("duration_s"), profile=res.get("profile"), dynamic=bool(delegate))
    _learn(r)
    if asked:
        out = _ask_user(r, "claude", asked, clean, res, run)
        out["route"] = _route_line(r)
        return out
    st = session_state(r["session_id"])
    limit = usage_limit_info(res)
    if res.get("ok") and st.get("takeover_declined"):
        st["takeover_declined"] = False  # Claude works again: the next limit may prompt again
        save_session_state(r["session_id"], st)
    if limit:
        r["status"] = "claude_limited"
        r.save()
        log_decision(event="claude_limit", request=r.id, detail=limit)
        if not agents().has("codex"):
            return {"request_id": r.id, "ok": False, "claude_usage_limit": limit,
                    "note": "Claude is at its usage limit and Codex isn't enabled on this Mac (Settings → Agents), "
                            "so nothing can take over. Say when the limit resets, in one line."}
        if st.get("takeover_declined"):
            return {"request_id": r.id, "ok": False, "claude_usage_limit": limit,
                    "note": "Claude is at its usage limit. Takeover was declined for this limit; the user can "
                            "type /takeover to let Codex continue. Say this in one line."}
        st["takeover_offer"] = r.id
        save_session_state(r["session_id"], st)
        return {"request_id": r.id, "ok": False, "claude_usage_limit": limit,
                "action": ("Use the clarify tool with ONE question: 'Claude hit its usage limit (" + limit[:120]
                           + "). Let Codex take over this session from where Claude stopped?' with choices "
                           "['Yes, Codex takes over', 'No']. Then call cc_takeover(request_id=\"" + r.id
                           + "\", accept=<true if Yes>). If clarify is unavailable, ask the same as plain text.")}
    out = {
        "request_id": r.id,
        "route": _route_line(r) if not force else f"Route: Claude {choice['name']} — {_WHO['codex']} is at its limit, so its equivalent took this one",
        "ok": res.get("ok"),
        "executor": f"Claude Code (claude -p --model {claude_model})" if claude_model else "Claude Code (claude -p)",
        "billing": res.get("billing"),
        "project": cwd,
        "attachments_passed": [e["stored_path"] for e in manifest.items],
        "claude_result": res.get("result"),
        "error": res.get("error"),
        "permission_denials": res.get("permission_denials"),
        "duration_s": res.get("duration_s"),
        **_live_messages_note(res),
        **_stopped_note(res),
    }
    return _relay(r, out, _reply_body(r, res, clean=clean, started=started))


def _stopped_note(res: dict) -> dict:
    """The user pressed Stop on the live Agents block (sidecar POST /worker/stop)."""
    if not res.get("stopped_by_user"):
        return {}
    return {"stopped": "stopped by you",
            "note": "The user stopped this run with the Stop button. Say in one line that it was stopped by "
                    "you (the user) and that the work done so far stays in the project; don't retry it."}


def _live_messages_note(res: dict) -> dict:
    """The user's chat messages that reached the running worker (LiveInput). The hub only saw the first
    prompt, so without this its reply follows that alone (e.g. "reply with just the number")."""
    msgs = [m for m in (res.get("live_messages") or []) if m]
    if not msgs:
        return {}
    quoted = "; ".join(json.dumps(m, ensure_ascii=False) for m in msgs)
    return {"user_messages_during_run": msgs,
            "note": f"While Claude worked, the user added these messages (Claude received them): {quoted}. Your "
                    f"reply must cover them as well as the original request; where they conflict, the later "
                    f"messages take priority."}


def _takeover_prompt(r: Request) -> str:
    """Compacted session context for Astra: request + brief + where Claude stopped + repo state."""
    res = r.data.get("result") or {}
    sid = (r.data.get("executor") or {}).get("session_id") or res.get("session_id")
    st = session_state(r["session_id"])
    convo = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in st.get("recent", [])[-10:])
    cwd = _cwd(r)
    parts = ["You are taking over from Claude Code, which stopped (usage limit) or is paused for this session.",
             "Continue the work from where it stopped. Do not redo finished work; check the repo state first.",
             "Be exhaustive in execution, minimal in words. Final message: Done / Verified / Note bullets.",
             "To show the user an image while you work (screenshot, render), write a line "
             "`ATTACH: /absolute/path.png` (png/jpg/webp/gif inside the project); it appears in the chat at once.",
             questions.QUESTION_RULES,
             "", _claude_prompt(r, Manifest(r.dir)),
             "", "RECENT SESSION CONVERSATION (compacted):", convo or "(none)",
             "", "WHERE CLAUDE STOPPED (tail of its transcript):",
             claude_transcript_tail(cwd, sid) or "(no transcript - Claude may not have started)",
             "", f"REPO STATE ({cwd}):", git_state(cwd)]
    return "\n".join(parts)


def _codex_worker_prompt(r: Request, dynamic: bool = False) -> str:
    """Codex as the conversation's chosen executor: the same worker rules + request Claude would get
    (+ the hand-off rules when the delegate server is attached)."""
    cfg = load_config()
    ecfg = cfg.get("executor", {}) or {}
    rules = worker_rules("codex", dynamic=dynamic, max_subagents=ecfg.get("max_subagents", 2),
                         hints=(cfg.get("dynamic_agents", {}) or {}).get("hints"))
    return rules + "\n\n" + _claude_prompt(r, Manifest(r.dir))


def _astra_takeover(r: Request, reason: str, choice: dict | None = None, feed=None, offer: bool = True) -> dict:
    """Codex executes the request: Astra takeover, or (choice) the OpenAI model the user picked."""
    cfg = load_config()
    cwd = _cwd(r)
    answer = r.data.get("pending_answer")
    thread = (r.data.get("executor") or {}).get("thread_id") if answer else None
    model = choice["model"] if choice else ((cfg.get("takeover", {}) or {}).get("model")
                                            or _takeover_peer(r) or (cfg.get("planner", {}) or {}).get("model"))
    # a takeover exists because Claude is out: only a picked-Codex run may offer Claude instead
    blocked = _limit_block(r, "codex", model, offer=bool(choice) and offer)
    effort = (choice or {}).get("effort") or (r.data.get("executor") or {}).get("effort") or _auto_effort(r)
    if blocked and blocked.get("switch"):  # Codex is at its limit: its Claude equivalent does this one
        return _execute(r, force={"provider": "anthropic", "model": blocked["switch"]["model"], "effort": effort},
                        feed=feed)
    if blocked:
        return blocked
    manifest = Manifest(r.dir)
    add_dirs = ([str(manifest.dir)] if manifest.items else []) + list(r.data.get("extra_dirs") or [])
    run = AgentRun.load(r.id, r["session_id"])
    run.attach_roots = [cwd, *add_dirs, str(r.dir)]
    who = (f"Codex {choice['name']}" + (f" ({choice['effort']})" if choice.get("effort") else "")) if choice else "Codex"
    run.start_main(who + (" · answered" if answer else ("" if choice else " · takeover")), "astra",
                   description="executor (Codex)", model=model)
    run.save(force=True)
    delegate = _delegate_env(r, run, parent="codex", cwd=cwd, add_dirs=add_dirs, model=model,
                             effort=effort) if choice else None
    if answer and thread:
        prompt = questions.answer_prompt(r.data.get("question") or {}, answer)
        r.write_text(f"answer-{len(r.data.get('question_log') or [])}.md", prompt)
    elif choice:
        prompt = _codex_worker_prompt(r, dynamic=bool(delegate)) + (
            f"\n\nTHE USER ANSWERED YOUR QUESTIONS:\n{answer}" if answer else "")
        r.write_text("codex_prompt.md", prompt)
    else:
        prompt = _takeover_prompt(r) + (f"\n\nTHE USER ANSWERED YOUR QUESTIONS:\n{answer}" if answer else "")
        r.write_text("takeover_prompt.md", prompt)
    r["status"] = "executing"
    r.save()
    _notify(f"Using {who}{'' if choice else ' (takeover)'}: {cwd}")
    timings: dict = {}
    started = time.time()
    res = run_astra_executor(prompt, cwd=cwd, add_dirs=add_dirs, cfg=cfg,
                             pid_file=LOCKS_DIR / f"takeover-{r['session_id'][:60]}.pid",
                             on_event=_agent_events(run, "codex"), resume_thread=thread,
                             model=choice["model"] if choice else model,
                             effort=effort,
                             stop_marker=r.dir / "stopped_by_user", delegate=delegate, timings=timings)
    r["pending_answer"] = None
    r["executor"] = {"provider": "codex-cli" if choice else "codex-cli (takeover)", "model": res.get("model"),
                     "effort": effort, "auto_effort": not (choice or {}).get("effort"), "chosen": bool(choice),
                     "billing": res.get("billing"), "thread_id": res.get("thread_id"), "dynamic": bool(delegate)}
    r["timings"] = {**(r.data.get("timings") or {}), **{k: round(v, 3) for k, v in timings.items()}}
    r["result"] = res
    r["status"] = "done" if res.get("ok") else ("stopped" if res.get("stopped_by_user") else "failed")
    r.save()
    with run.lock:
        run.finish_main(bool(res.get("ok")), step=None if res.get("ok") else res.get("error"),
                        stopped=bool(res.get("stopped_by_user")))
        run.save(force=True)
    log_decision(event="codex_executed" if choice else "takeover_executed", request=r.id, ok=res.get("ok"),
                 model=res.get("model"), reason=reason, dynamic=bool(delegate))
    _learn(r)
    route = f"Route: {who} — {reason}" if choice else f"Route: Codex (takeover) — {reason}"
    clean, asked = questions.extract(res.get("result") or "") if res.get("ok") else ("", None)
    if asked:
        out = _ask_user(r, "astra", asked, clean, res, run)
        out["route"] = route
        return out
    out = {"request_id": r.id, "route": route, "ok": res.get("ok"),
           "executor": f"{'Codex' if choice else 'Codex'} ({res.get('model')})", "billing": res.get("billing"),
           "project": cwd, "result": res.get("result"), "error": res.get("error"),
           "note": "Start the reply with the route line." + ("" if choice else " Type /takeover to switch back to Claude."),
           **({"stopped": "stopped by you", "note": "Start the reply with the route line. " + _stopped_note(res)["note"]}
              if res.get("stopped_by_user") else {})}
    body = _reply_body(r, res, clean=clean, started=started)
    if not choice and res.get("ok"):
        body += "\n\n_Type /takeover to switch back to Claude._"
    return _relay(r, out, body)


def _tool_takeover(params, **kwargs):
    """Answer a limit offer. Old shape (a request id): Claude hit its limit mid-run, Codex takes over the
    session. New shape {rid, to, kind: switch, model}: the selected executor was at its limit before the
    task started; accept runs THIS request on the other provider (same-tier model)."""
    r = Request(str(params.get("request_id", "")))
    if not r.exists:
        return json.dumps({"ok": False, "error": "unknown request_id"})
    st = session_state(r["session_id"])
    offer = st.get("takeover_offer")
    st["takeover_offer"] = None
    if isinstance(offer, dict) and offer.get("kind") == "switch" and offer.get("rid") == r.id:
        save_session_state(r["session_id"], st)
        to = offer.get("to") or "codex"
        if not params.get("accept"):
            log_decision(event="limit_switch_declined", request=r.id, to=to)
            msg = (r.data.get("limits") or {}).get("message") or "the limit"
            return json.dumps({"ok": True, "message": f"OK, nothing was started ({msg}). Send it again after the reset."})
        if not agents().has(to):
            return json.dumps({"ok": False, "error": f"{_WHO[to]} isn't enabled on this Mac (Settings → Agents)."})
        log_decision(event="limit_switch_accepted", request=r.id, to=to, model=offer.get("model"))
        if to == "codex":
            ch = executor_choice.get_choice(r["session_id"])
            info = executor_choice.model_info("openai", offer.get("model") or "gpt-6-astra") or {"name": offer.get("model")}
            pick = {"provider": "openai", "model": offer.get("model") or "gpt-6-astra", "name": info["name"],
                    "effort": ch.get("effort")}
            return json.dumps(_astra_takeover(r, reason="Claude was near its limit; you chose GPT", choice=pick,
                                              offer=False))
        return json.dumps(_execute(r, force={"provider": "anthropic", "model": offer.get("model") or "claude-opus-5-5"}))
    if params.get("accept") and not agents().has("codex"):
        save_session_state(r["session_id"], st)
        return json.dumps({"ok": False, "error": "Codex isn't enabled on this Mac (Settings → Agents)."})
    if not params.get("accept"):
        st["takeover_declined"] = True
        save_session_state(r["session_id"], st)
        log_decision(event="takeover_declined", request=r.id)
        return json.dumps({"ok": True, "message": "Declined. Won't ask again until the next limit. /takeover enables it."})
    st["takeover"] = True
    save_session_state(r["session_id"], st)
    log_decision(event="takeover_enabled", request=r.id)
    return json.dumps(_astra_takeover(r, reason="Claude usage limit; user accepted"))


def _tool_claude_execute(params, **kwargs):
    rid = str(params.get("request_id", ""))
    early = _attach_job(rid)  # started from the routing hook: one run, this call just waits for it
    if early is not None:
        return json.dumps(early)
    r = Request(rid)
    if not r.exists:
        return json.dumps({"ok": False, "error": "unknown request_id"})
    if r["status"] == "answered":
        return json.dumps(_execute(r))
    if r["route"] == ASTRA and r["status"] != "approved":
        return json.dumps({"ok": False, "error": f"request {r.id} is on the planning route and not "
                           f"approved (status={r['status']}). Execution refused."})
    if r["status"] in ("executing", "done"):
        return json.dumps({"ok": False, "error": f"request already {r['status']}", "result": r["result"]})
    return json.dumps(_execute(r))


def _cmd_takeover(raw_args: str = "", session_id: str = "", session_key: str = "", **kwargs):
    # WebUI runs plugin commands without session context: use the session that spoke last.
    sid = session_id or (read_json(STATE_DIR / "last_session.json", {}) or {}).get("session_id") or "default"
    st = session_state(sid)
    if st.get("takeover"):
        st["takeover"] = False
        save_session_state(sid, st)
        pid_file = LOCKS_DIR / f"takeover-{sid[:60]}.pid"
        stopped = ""
        if pid_file.exists():
            try:
                import signal as _signal
                os.killpg(int(pid_file.read_text()), _signal.SIGTERM)
                stopped = " Stopped the running Codex job."
            except Exception:
                pass
        log_decision(event="takeover_disabled", session=sid)
        return f"Takeover off — Claude executes again.{stopped}"
    if not agents().has("codex"):
        return "Takeover needs Codex, and only Claude Code is enabled on this Mac (Settings → Agents)."
    st["takeover"], st["takeover_declined"] = True, False
    save_session_state(sid, st)
    log_decision(event="takeover_enabled", session=sid)
    limited = [d for d in (read_json(f, {}) for f in sorted(REQUESTS_DIR.glob("*/request.json"))[-20:])
               if d and d.get("session_id") == sid and d.get("status") == "claude_limited"]
    if limited:
        rid = limited[-1]["id"]
        try:
            if _CTX is not None and _CTX.inject_message(
                    f"{TAG}-resume Continue request {rid} with Codex (takeover).", session_key=session_key or None):
                return f"Takeover on — Codex is continuing {rid}."
        except Exception:
            pass
        return f"Takeover on. Say 'continue' to have Codex resume {rid}."
    return "Takeover on — Codex executes in this session until you type /takeover again."


def _plan_display(r: Request, data: dict, cfg: dict) -> str:
    head = f"**Planner: {_planner_label(cfg)}**"
    if data["kind"] == "questions":
        q = questions.from_planner(data.get("questions") or [])
        if q:
            rnd = sum(1 for t in r["planning_transcript"] if t["role"] == "planner"
                      and json.loads(t["content"]).get("kind") == "questions")
            return (f"{head}\n\n{data['summary']}\n\n"
                    + questions.block(q, request_id=r.id, source="astra-plan", round=rnd))
        return f"{head}\n\n{data['summary']}"
    body = [head, "", data["proposal_markdown"]]
    if data.get("decisions"):
        body += ["", "**Decisions to confirm:**"] + [f"- {d}" for d in data["decisions"]]
    if data.get("changed_points"):
        body += ["", "**Changed since last proposal:**"] + [f"- {c}" for c in data["changed_points"]]
    body += ["", "_Reply **yes / go** to approve, **yes but …** to approve with changes, describe "
                 "changes to revise, or **cancel**. Nothing executes until you approve._"]
    return "\n".join(body)


def _planner_cfg(r: Request, cfg: dict) -> tuple[dict, str, str | None]:
    """(cfg to plan with, note, refusal). The configured planner near its limit: plan with the other CLI
    (one Note line says so). Both near their limits (or the other not linked): refusal = one line."""
    pcfg = providers.planner_config(cfg)
    agent = limits_gate.agent_of(pcfg.get("provider", "codex-cli"))
    try:
        g = limits_gate.check(agent, pcfg.get("model"), purpose="work")
    except Exception:
        return cfg, "", None
    if not g.blocked:
        return cfg, "", None
    other = limits_gate.other(agent)
    log_decision(event="planner_limit", request=r.id, agent=agent, pct=g.pct)
    if agents().has(other) and providers.linked(other):
        planner = dict(cfg.get("planner", {}) or {})
        if other == "claude":
            planner.update(provider="claude-cli", model=planner.get("claude_model") or None, label="Claude")
        else:
            planner.update(provider="codex-cli", model=planner.get("codex_model") or "gpt-6-astra", label="GPT-6 Astra")
        if not limits_gate.check(other, planner.get("model"), purpose="work").blocked:
            return {**cfg, "planner": planner}, f"Note: planned with {planner['label']} because {g.message}.", None
    return cfg, "", f"{g.message}. Planning was not started."


def _planner_run(r: Request, cfg: dict, phase: str, manifest: Manifest):
    """run_planner with an agent record (Astra · discover / revise / finalize). Checks the limits first."""
    cfg, note, refusal = _planner_cfg(r, cfg)
    if refusal:
        return providers.PlanResult(False, None, "limits", "", refusal)
    run = AgentRun.load(r.id, r.data.get("session_id") or "")
    pcfg = providers.planner_config(cfg)
    kind = "codex" if pcfg.get("provider", "codex-cli") == "codex-cli" else "claude"
    names = {"discover": "planning", "revise": "revising the plan", "finalize": "writing the brief"}
    run.start_main(f"{pcfg.get('label') or 'Codex'} · {names.get(phase, phase)}",
                   "astra" if kind == "codex" else "claude", description="planner", model=pcfg.get("model"))
    run.save(force=True)
    res = run_planner(phase, r.data, manifest.render(), manifest.images(), cfg, r.dir / "planner",
                      on_event=_agent_events(run, "codex") if kind == "codex" else None)
    step = None
    if res.ok and res.data:
        step = {"questions": "Asked you questions", "proposal": "Proposed a plan",
                "brief": "Wrote the brief"}.get(res.data.get("kind"))
    run.finish_main(res.ok, waiting=bool(res.ok and res.data and res.data.get("kind") == "questions"),
                    step=step if res.ok else res.error)
    run.save(force=True)
    res.note = note
    res.cfg = cfg
    return res


def _tool_astra_plan(params, **kwargs):
    r = Request(str(params.get("request_id", "")))
    if not r.exists:
        return json.dumps({"ok": False, "error": "unknown request_id"})
    if r["route"] != ASTRA:
        return json.dumps({"ok": False, "error": "request is on the direct Claude route"})
    cfg = load_config()
    if r["pending_reply"]:
        r["planning_transcript"].append({"role": "user", "content": r["pending_reply"]})
        r["pending_reply"] = None
    phase = "revise" if r["planning_transcript"] else "discover"
    manifest = Manifest(r.dir)
    r["status"] = "planning"
    r.save()
    _notify(f"Using planner {_planner_label(cfg)}: {phase}")
    res = _planner_run(r, cfg, phase, manifest)
    r["planner"] = f"{res.provider}:{res.model}"
    if not res.ok:
        r["status"] = "awaiting_answers"  # stays open; the user can retry by replying
        r.save()
        log_decision(event="planner_failed", request=r.id, error=res.error)
        return json.dumps({"ok": False, "planner": r["planner"], "error": res.error,
                           "note": "Planner failed. Report this exactly; do not substitute another model."})
    data = res.data
    r["planning_transcript"].append({"role": "planner", "content": json.dumps(data)})
    r["status"] = "awaiting_answers" if data["kind"] == "questions" else "awaiting_approval"
    if data["kind"] != "questions":
        r["proposal"] = data
        r.write_text("proposal.md", data.get("proposal_markdown", ""))
    r.save()
    log_decision(event="planned", request=r.id, kind=data["kind"], planner=r["planner"])
    display = _plan_display(r, data, getattr(res, "cfg", cfg))
    if res.note:
        display += "\n\n" + res.note
    out = {"ok": True, "planner": r["planner"], "status": r["status"],
           "images_sent_to_planner": manifest.images(), "display": display}
    return json.dumps(_relay(r, out, display))


def _tool_execute_approved(params, **kwargs):
    r = Request(str(params.get("request_id", "")))
    if not r.exists:
        return json.dumps({"ok": False, "error": "unknown request_id"})
    if r["status"] != "approved":
        return json.dumps({"ok": False, "error": f"not approved (status={r['status']}). Refused."})
    cfg = load_config()
    manifest = Manifest(r.dir)
    proposal = r.data.get("proposal") or {}
    mode = str((cfg.get("planner", {}) or {}).get("finalize", "auto")).lower()
    note = ""
    if mode != "always" and not r["modifications"] and (proposal.get("brief_markdown") or "").strip():
        # approved unchanged: the proposal already carries the brief (P6), no second planner call
        data = {"kind": "brief", "brief_markdown": proposal["brief_markdown"], "changed_points": [],
                "specialists": proposal.get("specialists") or []}
        log_decision(event="finalize_skipped", request=r.id)
    else:
        _notify(f"Using planner {_planner_label(cfg)}: final execution brief")
        res = _planner_run(r, cfg, "finalize", manifest)
        if not res.ok or res.data.get("kind") != "brief" or not res.data.get("brief_markdown"):
            log_decision(event="brief_failed", request=r.id, error=res.error)
            return json.dumps({"ok": False, "error": f"final brief failed: {res.error or 'no brief returned'}",
                               "note": "Execution NOT started. Report this; the approval is kept so the user can retry."})
        data, note = res.data, res.note
        r["planner"] = f"{res.provider}:{res.model}"
    r["brief"] = data["brief_markdown"]
    r.write_text("brief.md", r["brief"])
    r.save()
    out = _execute(r)
    out["changed_points"] = data.get("changed_points", [])
    out["specialists_planned"] = data.get("specialists", [])
    out["brief_path"] = str(r.dir / "brief.md")
    out["plan_embed"] = f"[[plan:{r.id}]]"  # the reply shows the brief behind this, collapsed (plan-chip.js)
    if out.get("reply"):  # verbatim relay: the brief chip and changed points go into the shown reply
        tok = _REPLY_RX.search(out["reply"]).group(2)
        f = r.dir / f"reply-{tok}.md"
        extra = [f"[[plan:{r.id}]]", ""] + [f"- Changed: {c}" for c in data.get("changed_points") or []]
        if note:
            extra += [note]
        f.write_text("\n".join(extra).strip() + "\n\n" + f.read_text())
    if r.data.get("session_id"):
        set_active(r["session_id"], None)
    return json.dumps(out)


def _tool_set_project(params, **kwargs):
    path, msg = projects.set_current(str(params.get("project", "")))
    return json.dumps({"ok": bool(path), "message": msg})


def _status_text() -> str:
    cfg = load_config()
    planner = providers.planner_config(cfg)
    exposed = planner.get("model") in codex_models() if planner.get("provider") == "codex-cli" else None
    recent = sorted((STATE_DIR / "requests").glob("*/request.json"))[-5:]
    lines = [f"Project: {projects.current() or '(none)'}",
             f"Planner: {_planner_label(cfg)}" + ("" if exposed is None else f" — exposed: {exposed}"),
             f"Executor: Claude Code (claude -p), max_parallel={cfg.get('executor', {}).get('max_parallel', 1)}",
             "Recent requests:"]
    for f in reversed(recent):
        d = read_json(f, {}) or {}
        lines.append(f"  {d.get('id')}  {d.get('route')}  {d.get('status')}  {d.get('original_prompt', '')[:60]!r}")
    return "\n".join(lines)


def _tool_status(params, **kwargs):
    return json.dumps({"ok": True, "status": _status_text()})


def _cmd_cc(raw_args: str = "", session_id: str = "", **kwargs):
    args = (raw_args or "").strip()
    sub, _, rest = args.partition(" ")
    sub = sub.lower()
    if sub in ("", "status"):
        return _status_text()
    if sub == "project":
        if not rest:
            return f"Current project: {projects.current() or '(none)'}"
        return projects.set_current(rest)[1]
    if sub == "workspace":
        parts = rest.split()
        if len(parts) < 2 or parts[0] != "create" and len(parts) < 2:
            return "Usage: /cc workspace create <name> <folder-or-project> [<folder-or-project> ...]"
        if parts[0] == "create":
            parts = parts[1:]
        return projects.create_workspace(parts[0], parts[1:])[1]
    if sub == "projects":
        return "\n".join(f"{n}: {p}" for n, p in sorted(projects.known_projects().items()))
    if sub == "cancel":
        r = active_planning_request(session_id or "default")
        if not r:
            return "No open planning request in this session."
        r["status"] = "cancelled"
        r.save()
        set_active(session_id or "default", None)
        return f"Cancelled {r.id}."
    if sub == "route":
        d = classify(rest, load_config())
        return f"Would route: {d.route} — {d.reason} (score {d.score}{', forced' if d.forced else ''})"
    if sub == "planner":
        cfg = load_config()
        return f"{_planner_label(cfg)}\nCodex models exposed to your ChatGPT login: {', '.join(codex_models())}"
    return "Usage: /cc [status | project <name|path> | projects | cancel | route <text> | planner]"


def register(ctx):
    global _CTX
    _CTX = ctx
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
    # Verbatim relay needs a Hermes that can hold the stream until the rewrite (hold_stream). Older ones
    # (TypeError / unknown hook) keep today's relay: the hub rewrites the worker's result.
    try:
        ctx.register_hook("transform_llm_output", _on_transform_llm_output, hold_stream=True)
        _RELAY["on"] = True
    except Exception as exc:
        _RELAY["on"] = False
        log_decision(event="relay_unavailable", error=repr(exc)[:200])
    for hook, fn in (("agent_loop_stopped", _on_agent_loop_stopped), ("pre_api_request", _on_api_request),
                     ("post_api_request", _on_api_response)):
        try:
            ctx.register_hook(hook, fn)
        except Exception:
            pass

    def tool(name, desc, props, required, handler, emoji):
        ctx.register_tool(
            name=name, toolset="command_centre", handler=handler, emoji=emoji,
            schema={"name": name, "description": desc,
                    "parameters": {"type": "object", "properties": props, "required": required}},
        )

    rid = {"request_id": {"type": "string", "description": "Request id from the [command-centre] directive"}}
    tool("cc_claude_execute",
         "Run a routed request in Claude Code (Claude Max subscription) in the request's project, passing the "
         "user's verbatim prompt and ORIGINAL attachments. Use only when a [command-centre] directive says so.",
         rid, ["request_id"], _tool_claude_execute, "")
    tool("cc_astra_plan",
         "Run the planner (Codex) for a planning-route request: returns material questions or a proposal to show "
         "the user. Never executes anything.", rid, ["request_id"], _tool_astra_plan, "")
    tool("cc_execute_approved",
         "After the user approved a plan: get the final brief from the planner, then run Claude Code with the brief "
         "+ original prompt + original attachments. Refuses unless the request is approved.",
         rid, ["request_id"], _tool_execute_approved, "")
    tool("cc_set_project", "Switch the command centre's current project (name or path).",
         {"project": {"type": "string"}}, ["project"], _tool_set_project, "")
    tool("cc_create_workspace",
         "Create a multi-folder workspace (e.g. an app spread across several repos). Folders may be paths or "
         "known project names; the first is primary (Claude's working dir) unless `primary` is given. It appears "
         "in the WebUI workspace picker.",
         {"name": {"type": "string"}, "folders": {"type": "array", "items": {"type": "string"}},
          "primary": {"type": "string"}}, ["name", "folders"],
         lambda params, **kw: json.dumps(dict(zip(("ok", "message"), projects.create_workspace(
             str(params.get("name", "")), list(params.get("folders") or []), params.get("primary") or None)))),
         "")
    tool("cc_takeover",
         "Answer the Claude-usage-limit takeover offer. accept=true: Codex (ChatGPT subscription) continues the "
         "request from where Claude stopped and stays the executor for this session until /takeover. accept=false: decline "
         "(no more offers until the next limit).",
         {"request_id": {"type": "string"}, "accept": {"type": "boolean"}}, ["request_id", "accept"],
         _tool_takeover, "")
    tool("cc_status", "Show command-centre status: project, planner, recent requests.", {}, [], _tool_status, "")
    ctx.register_command("takeover", _cmd_takeover,
                         description="Toggle Codex takeover (executes instead of Claude) for this session")
    ctx.register_command("cc", _cmd_cc, description="Command Centre: status, project, cancel, route, planner",
                         args_hint="[status|project <name>|projects|cancel|route <text>|planner]")

    def _ensure_services():
        # A service added by an update (e.g. the cc-sidecar on installs older than it) gets installed
        # the first time Tabbie starts with the new code, so nobody has to re-run setup for it.
        try:
            from . import updates
            done = updates.ensure_services()
            if done:
                log_decision(event="services_installed", labels=done)
            reaped = updates.reap_orphans()
            if reaped:
                log_decision(event="orphans_settled", requests=reaped)
            _hmem()
            from hmem import projects as hprojects
            if hprojects.label_all_webui_workspaces():
                log_decision(event="webui_workspaces_labelled")
        except Exception as exc:
            log_decision(event="services_check_failed", error=repr(exc)[:200])
    threading.Thread(target=_ensure_services, daemon=True, name="tabbie-ensure-services").start()
