"""Prep pass: a cheaper model reads first, so the model the user picked doesn't pay to read everything itself.

Two jobs, run side by side and only when they pay off:
  - digest: the conversation so far (or a shared team chat's history) boiled down to bullets. Used instead of
    reopening a big worker session, and instead of the raw history in a fresh one.
  - scout: in a project, read-only look around for what the request touches: the relevant files and lines,
    how they fit together, conventions and gotchas. The chosen model starts from that map.

The cheap model is the same provider's mid tier: Sonnet when the pick is Claude, GPT Terra when it's OpenAI
(prep.models in router.yaml). Skipped when the pick is already that tier or cheaper, for linked model
providers, Motion, answers to a worker's questions, and quick requests. Whatever goes wrong here, the run
goes ahead without notes: prep can only save money, never block work.
"""
from __future__ import annotations

import json
import re
import shutil
import tempfile
import threading
import time
from pathlib import Path

from . import executor_choice

DEFAULTS = {
    "enabled": True,
    "models": {"claude": "claude-sonnet-5-5", "codex": "gpt-6.1-terra"},
    "digest_min_chars": 8000,     # a conversation longer than this gets a digest instead of raw turns
    "reopen_digest_ctx": 30000,   # a worker session bigger than this (tokens) starts fresh with a digest instead
    "scout": True,
    "scout_min_chars": 60,        # requests shorter than this ("make it blue") skip the scout
    "timeout_seconds": 120,
}
SHARED_HEAD = "This message comes from the shared team chat"
SHARED_SPLIT = "\n\n---\n\n"
CHEAP_TIERS = ("mid", "fast")
_SETUP = threading.Lock()
_QUICK = re.compile(r"^\s*(thanks?|thank you|ok(ay)?|yes|no|go|continue|do it|ship it|looks good|nice|great)\b[\s.!]*$", re.I)


def settings(cfg: dict) -> dict:
    p = dict(DEFAULTS)
    p.update((cfg or {}).get("prep") or {})
    p["models"] = {**DEFAULTS["models"], **((cfg or {}).get("prep") or {}).get("models", {})}
    return p


def split_shared(text: str) -> tuple[str, str] | None:
    """A message run from the team site carries the shared chat's history: (history, the actual request)."""
    if not text.startswith(SHARED_HEAD) or SHARED_SPLIT not in text:
        return None
    head, _, ask = text.rpartition(SHARED_SPLIT)
    return head, ask


def plan(cfg: dict, *, worker: str, provider: str, model: str | None, message: str, conversation_chars: int,
         reopen_ctx: int | None, project: bool, skip: bool = False) -> dict:
    """What to run. worker: claude | codex. reopen_ctx: tokens in the session a follow-up would reopen (None:
    nothing to reopen). Returns {"digest": bool, "scout": bool, "reopen": bool, "model": cheap model}."""
    p = settings(cfg)
    none = {"digest": False, "scout": False, "reopen": reopen_ctx is not None, "model": None}
    cheap = p["models"].get(worker)
    if skip or not p.get("enabled") or not cheap or worker not in ("claude", "codex"):
        return none
    if model and executor_choice.tier_of(provider, model) in CHEAP_TIERS:
        return none  # the pick is already the cheap tier: nothing to save
    shared = split_shared(message)
    ask = shared[1] if shared else message
    big_reopen = reopen_ctx is not None and reopen_ctx >= int(p["reopen_digest_ctx"])
    long_chat = conversation_chars >= int(p["digest_min_chars"]) or (shared and len(shared[0]) >= int(p["digest_min_chars"]) // 2)
    digest = bool(big_reopen or (reopen_ctx is None and long_chat))
    reopen = reopen_ctx is not None and not big_reopen
    scout = bool(p.get("scout") and project and not reopen and len(ask.strip()) >= int(p["scout_min_chars"])
                 and not _QUICK.match(ask))
    return {"digest": digest, "scout": scout, "reopen": reopen, "model": cheap if (digest or scout) else None}


# ---------- prompts ----------
DIGEST_PROMPT = """You are preparing notes for another AI model that will handle the user's next request.
Below is the conversation so far. Boil it down to the bullets that model needs:
- what the user is working on and wants overall
- decisions made, preferences and constraints the user stated (keep their exact words for rules)
- what has been done already (files, commands, results) and what is still open
- names, paths, numbers, IDs and links that later messages may refer to ("it", "that file", "the site")
Leave out chit-chat and anything superseded. Plain bullets, no preamble, at most about 40 bullets.
Do not answer or act on the request yourself.

THE NEXT REQUEST (for context only):
{ask}

THE CONVERSATION SO FAR (oldest first):
{conversation}
"""

SCOUT_PROMPT = """You are scouting a project for another AI model that will do the work below. Read only: never edit,
create or run anything. Spend your effort finding what that model will need, quickly:
- the files and line ranges that matter for this request (exact paths), and what each does
- how they connect (call sites, config, tests that cover them)
- conventions to follow (naming, structure, test command) and gotchas you notice
- anything that looks already done or missing
Answer as short bullets with exact paths. No preamble, no plan for the work, no code changes. At most about 35 bullets.

THE REQUEST:
{ask}
"""


def _run_claude(prompt: str, model: str, cwd: str, add_dirs: list[str], tools: bool, timeout: int) -> str:
    from .providers import _ANTHROPIC_BILLING_VARS, _clean_env, _run
    from . import worker_profile
    if not shutil.which("claude"):
        return ""
    with _SETUP:  # both passes start together: write the shared settings files once, not at the same time
        files = worker_profile.ensure({})
        mcp = worker_profile.mcp_config(worker_profile.WORKER_DIR / "mcp-empty.json")
    cmd = ["claude", "-p", "--output-format", "json", "--model", model, "--permission-mode", "default",
           "--setting-sources", "project,local", "--settings", files["settings"], "--strict-mcp-config",
           "--mcp-config", mcp, "--exclude-dynamic-system-prompt-sections"]
    cmd += ["--allowedTools", "Read", "Grep", "Glob"] if tools else []
    cmd += ["--disallowedTools", "Bash", "Edit", "Write", "MultiEdit", "NotebookEdit", "WebFetch", "WebSearch", "Task",
            "Agent", "Workflow", *([] if tools else ["Read", "Grep", "Glob"])]
    for d in add_dirs:
        cmd += ["--add-dir", d]
    env = _clean_env(_ANTHROPIC_BILLING_VARS)
    env["TABBIE_PREP"] = "1"
    code, out, _err = _run(cmd, cwd=cwd, env=env, timeout=timeout, stdin=prompt)
    if code != 0:
        return ""
    try:
        return str(json.loads(out).get("result") or "").strip()
    except ValueError:
        return ""


def _run_codex(prompt: str, model: str, cwd: str, add_dirs: list[str], timeout: int) -> str:
    from .providers import _clean_env, _run, build_codex_exec_cmd
    if not shutil.which("codex"):
        return ""
    with tempfile.TemporaryDirectory(prefix="tabbie-prep-") as tmp:
        out_file = Path(tmp) / "out.md"
        cmd = build_codex_exec_cmd(model=model, effort="low", out_file=out_file, cwd=cwd, add_dirs=add_dirs,
                                   sandbox="read-only", ephemeral=True)
        code, _out, _err = _run(cmd, cwd=cwd, env=_clean_env(()), timeout=timeout, stdin=prompt)
        try:
            return out_file.read_text(encoding="utf-8").strip() if code == 0 else ""
        except OSError:
            return ""


def _ask(worker: str, prompt: str, model: str, cwd: str, add_dirs: list[str], tools: bool, timeout: int) -> str:
    if not tools:  # the digest reads only the conversation: run it away from the project and its context
        with tempfile.TemporaryDirectory(prefix="tabbie-digest-") as empty:
            return _ask_in(worker, prompt, model, empty, [], tools, timeout)
    return _ask_in(worker, prompt, model, cwd, add_dirs, tools, timeout)


def _ask_in(worker: str, prompt: str, model: str, cwd: str, add_dirs: list[str], tools: bool, timeout: int) -> str:
    try:
        if worker == "codex":
            return _run_codex(prompt, model, cwd, add_dirs, timeout)
        return _run_claude(prompt, model, cwd, add_dirs, tools, timeout)
    except Exception:  # noqa: BLE001 (prep never blocks the run)
        return ""


def run(cfg: dict, what: dict, *, worker: str, message: str, conversation: str, cwd: str, add_dirs: list[str],
        on_progress=None) -> dict:
    """Run the planned passes side by side. Returns {"notes": text for the worker prompt ("" if nothing came
    back), "digest": bool, "scout": bool, "seconds": float}."""
    p = settings(cfg)
    timeout = int(p["timeout_seconds"])
    shared = split_shared(message)
    ask = shared[1] if shared else message
    if shared:
        conversation = (shared[0] + "\n\n" + conversation).strip()
    out: dict[str, str] = {}
    jobs = []
    if what.get("digest") and conversation.strip():
        prompt = DIGEST_PROMPT.format(ask=ask[:4000], conversation=conversation[-120_000:])
        jobs.append(("digest", prompt, False))
    if what.get("scout"):
        jobs.append(("scout", SCOUT_PROMPT.format(ask=ask[:6000]), True))
    if not jobs:
        return {"notes": "", "digest": False, "scout": False, "seconds": 0.0}
    if on_progress:
        names = executor_choice.model_info("anthropic" if worker == "claude" else "openai", what["model"]) or {}
        on_progress(f"Reading ahead with {names.get('name') or what['model']}"
                    + (" (the chat so far and the project)" if len(jobs) == 2 else
                       " (the chat so far)" if jobs[0][0] == "digest" else " (the project)"))
    started = time.time()

    def go(name, prompt, tools):
        out[name] = _ask(worker, prompt, what["model"], cwd, add_dirs, tools, timeout)

    threads = [threading.Thread(target=go, args=j, daemon=True) for j in jobs]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout + 15)
    parts = []
    if out.get("digest"):
        parts.append("THE CONVERSATION SO FAR (summarised by a quicker model; the user's latest request above is "
                     "the source of truth):\n" + out["digest"][:20_000])
    if out.get("scout"):
        parts.append("PROJECT SCOUT NOTES (a quicker model looked around read-only; confirm a detail before you "
                     "rely on it, and read further where they're thin):\n" + out["scout"][:20_000])
    return {"notes": "\n\n".join(parts), "digest": bool(out.get("digest")), "scout": bool(out.get("scout")),
            "seconds": round(time.time() - started, 1)}


def conversation_text(messages: list, current: str = "", cap: int = 200_000) -> str:
    """WebUI session messages → plain "ROLE: text" lines, oldest first, without the current request."""
    lines = []
    for m in messages or []:
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant"):
            continue
        c = m.get("content")
        if isinstance(c, list):
            c = " ".join(str(x.get("text", "")) for x in c if isinstance(x, dict))
        c = str(c or "").strip()
        if c:
            lines.append(f"{m['role'].upper()}: {c}")
    if lines and current and lines[-1] == f"USER: {current.strip()}":
        lines.pop()
    text = "\n\n".join(lines)
    return text[-cap:]

