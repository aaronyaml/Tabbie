"""Planner and executor providers.

Planner providers return a structured PlanResult; executor providers run Claude Code.
Both verify subscription (OAuth) authentication before running and strip API-key
environment variables so a stray key can never silently switch billing.
"""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .common import LOCKS_DIR, read_json
from .questions import QUESTION_ITEM_SCHEMA, QUESTION_RULES

_ANTHROPIC_BILLING_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
                           "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")
_OPENAI_BILLING_VARS = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "CODEX_API_KEY")

PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["kind", "summary", "questions", "proposal_markdown", "decisions",
                 "brief_markdown", "changed_points", "specialists"],
    "properties": {
        "kind": {"type": "string", "enum": ["questions", "proposal", "brief"]},
        "summary": {"type": "string"},
        "questions": {"type": "array", "items": QUESTION_ITEM_SCHEMA},
        "proposal_markdown": {"type": "string"},
        "decisions": {"type": "array", "items": {"type": "string"}},
        "brief_markdown": {"type": "string"},
        "changed_points": {"type": "array", "items": {"type": "string"}},
        "specialists": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["tool", "purpose", "attachment_indexes"],
                "properties": {
                    "tool": {"type": "string", "enum": ["higgsfield", "elevenlabs", "other"]},
                    "purpose": {"type": "string"},
                    "attachment_indexes": {"type": "array", "items": {"type": "integer"}},
                },
            },
        },
    },
}

PLANNER_ROLE = """You are the PLANNER in a personal AI command centre. Your roles: planner, architect,
senior product thinker, creative director, requirements analyst, design critic, prompt refiner.
You do NOT write the implementation — Claude Code does that after the user approves your direction.

Rules:
- Ask questions ONLY when the answer materially changes what gets built. Never ask for the sake of it.
- Consolidate questions into ONE concise round (1-4). If intent is already clear, skip
  questions and go straight to a proposal.
- Each question is a card: header = a 1-2 word chip (max 12 chars), question = the full question,
  options = 2-4 concrete choices (label 1-5 words + one-line description of the consequence),
  recommended option first; multiSelect=true only when several can apply together. The user can
  always type their own answer instead.
- A proposal = the recommended direction plus the main decisions the user should confirm. Short
  bullets, concrete; no essays, no restating the request, no filler.
- Specialist tools available after approval: Higgsfield (image/video/ad creative, 3D, some audio) and
  ElevenLabs (voiceover/TTS, SFX, music, dubbing, transcription). Only include them when they add
  real value. Never plan paid generation before approval.
- Attachments: the ORIGINAL files are listed with absolute paths; images are also attached to you
  directly. You may read the project and attachment files (read-only). Refer to attachments by
  their [index] and path; Claude will receive the same originals.
- Output must match the JSON schema. Unused fields: empty string / empty array."""

PHASE_INSTRUCTIONS = {
    "discover": "PHASE: discovery. Return kind='questions' (material questions only) or, if intent is "
                "clear enough, kind='proposal' with proposal_markdown + decisions.",
    "revise": "PHASE: revision. The user replied (latest user turn). Incorporate it. Return "
              "kind='proposal' (preferred) or, only if something critical is still unknown, "
              "kind='questions'. If this revises an earlier proposal, list materially changed points "
              "in changed_points.",
    "finalize": "PHASE: final execution brief. The user APPROVED the latest proposal (with any "
                "modifications shown in the transcript — apply them; do not reopen discovery). "
                "Return kind='brief' with brief_markdown: a complete, self-contained execution "
                "brief for Claude Code — goal, scope, decisions, file/area targets, how each "
                "attachment [index] is used, specialist steps (only if needed, after approval), "
                "acceptance criteria and verification steps. SEMANTIC COMPRESSION: keep every "
                "meaningful requirement, decision and constraint; drop conversational filler, "
                "repeated questions, duplicated requirements and explanation. Do not restate the "
                "original request or the attachment list (Claude receives both separately); "
                "reference attachments by [index]. Put any approval modifications you applied in "
                "changed_points. Fill specialists.",
}


@dataclass
class PlanResult:
    ok: bool
    data: dict | None
    provider: str
    model: str
    error: str | None = None
    raw: str = ""


def _clean_env(drop: tuple[str, ...]) -> dict:
    env = dict(os.environ)
    for k in drop:
        env.pop(k, None)
    env["HERMES_WORKER"] = "1"  # hmem refuses user-only actions (approve, reject, add as user…) from workers
    return env


def _run(cmd: list[str], *, cwd: str, env: dict, timeout: int, stdin: str | None = None) -> tuple[int, str, str]:
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        out, err = proc.communicate(stdin, timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGTERM)  # whole tree: no orphaned workers or dev servers
        try:
            out, err = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            out, err = proc.communicate()
        return 124, out or "", (err or "") + f"\nTIMEOUT after {timeout}s"
    return proc.returncode, out or "", err or ""


def _run_stream(cmd: list[str], *, cwd: str, env: dict, timeout: int, stdin: str,
                on_line, on_start=None) -> tuple[int, str, str]:
    """Like _run, but hands each stdout line to on_line as it arrives (stream-json workers)."""
    import threading
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, bufsize=1, start_new_session=True)
    if on_start:
        on_start(proc)
    err_buf: list[str] = []
    threading.Thread(target=lambda: err_buf.append(proc.stderr.read()), daemon=True).start()
    timed_out = threading.Event()

    def _kill():
        timed_out.set()
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(proc.pid, sig)  # whole tree: no orphaned workers or dev servers
            except ProcessLookupError:
                return
            time.sleep(10)
    timer = threading.Timer(timeout, _kill)
    timer.daemon = True
    timer.start()
    try:
        proc.stdin.write(stdin)
        proc.stdin.close()
        out_lines = []
        for line in proc.stdout:
            out_lines.append(line)
            try:
                on_line(line)
            except Exception:
                pass  # a UI sink failing must never fail the run
        proc.wait()
    finally:
        timer.cancel()
    err = "".join(err_buf) + (f"\nTIMEOUT after {timeout}s" if timed_out.is_set() else "")
    return (124 if timed_out.is_set() else proc.returncode), "".join(out_lines), err


def _short(s, n: int) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


_TOOL_VERBS = {"Read": "Reading", "Edit": "Editing", "MultiEdit": "Editing", "Write": "Writing",
               "NotebookEdit": "Editing", "Bash": "Running:", "Grep": "Searching for", "Glob": "Finding",
               "WebFetch": "Fetching", "WebSearch": "Searching the web for", "Task": "Delegating:",
               "Agent": "Delegating:", "Skill": "Using skill", "TodoWrite": "Updating the task list"}


def describe_claude_event(ev: dict) -> str | None:
    """One stream-json event -> a short human line for the live activity feed (None = skip)."""
    t = ev.get("type")
    if t == "system" and ev.get("subtype") == "init":
        return f"Claude Code started · {ev.get('model', '')} · {ev.get('cwd', '')}"
    if t == "assistant":
        out = []
        for b in (ev.get("message") or {}).get("content") or []:
            bt = b.get("type")
            if bt == "thinking" and b.get("thinking"):
                out.append("Thinking: " + _short(b["thinking"], 500))
            elif bt == "text" and b.get("text", "").strip():
                out.append(_short(b["text"], 600))
            elif bt == "tool_use":
                i = b.get("input") or {}
                arg = (i.get("description") or i.get("file_path") or i.get("command") or i.get("pattern")
                       or i.get("url") or i.get("path") or i.get("query") or i.get("skill") or "")
                verb = _TOOL_VERBS.get(b.get("name"), b.get("name"))
                if b.get("name") in ("Read", "Edit", "Write", "MultiEdit", "NotebookEdit") and "/" in str(arg):
                    arg = str(arg).rsplit("/", 1)[-1]  # file name is enough for a status line
                out.append(f"{verb} {_short(arg, 140)}" if arg else str(verb))
        return "\n".join(out) or None
    if t == "user":
        for b in (ev.get("message") or {}).get("content") or []:
            if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("is_error"):
                c = b.get("content")
                c = c if isinstance(c, str) else " ".join(x.get("text", "") for x in c or [] if isinstance(x, dict))
                return "Error: " + _short(c, 200)
        return None
    if t == "result":
        return f"Finished · {round((ev.get('duration_ms') or 0) / 1000)}s · {ev.get('num_turns')} turns"
    return None


# --- auth verification ----------------------------------------------------------------------

_CLAUDE_AUTH_OK = {"at": 0.0}  # last time `claude auth status` confirmed subscription OAuth
_AUTH_FRESH_S, _AUTH_GRACE_S = 600, 6 * 3600


def claude_auth() -> tuple[bool, str]:
    if not shutil.which("claude"):
        return False, "claude CLI not found on PATH"
    import time
    ok_age = time.time() - _CLAUDE_AUTH_OK["at"]
    if ok_age < _AUTH_FRESH_S:  # confirmed recently: skip the subprocess
        return True, "Claude subscription (claude.ai OAuth)"
    code, out, err = _run(["claude", "auth", "status"], cwd=str(Path.home()),
                          env=_clean_env(_ANTHROPIC_BILLING_VARS), timeout=30)
    try:
        st = json.loads(out)
    except json.JSONDecodeError:
        # A slow/hung check (machine under heavy load) shouldn't block work when the same login was
        # confirmed recently; a real logout still fails fast inside `claude -p` itself.
        if code == 124 and ok_age < _AUTH_GRACE_S:
            return True, "Claude subscription (claude.ai OAuth)"
        return False, f"could not read `claude auth status`: {(err or out)[:200]}"
    if not st.get("loggedIn"):
        return False, "Claude Code is not logged in (run `claude auth login`)"
    if st.get("authMethod") != "claude.ai" or st.get("apiProvider") != "firstParty":
        return False, f"Claude Code auth is {st.get('authMethod')}/{st.get('apiProvider')}, not Claude subscription OAuth"
    _CLAUDE_AUTH_OK["at"] = time.time()
    return True, "Claude subscription (claude.ai OAuth)"


def codex_auth() -> tuple[bool, str]:
    if not shutil.which("codex"):
        return False, "codex CLI not found on PATH"
    code, out, err = _run(["codex", "login", "status"], cwd=str(Path.home()),
                          env=_clean_env(_OPENAI_BILLING_VARS), timeout=30)
    text = (out + err).strip()
    if "ChatGPT" in text:
        return True, "ChatGPT subscription (Codex OAuth)"
    return False, f"Codex is not on ChatGPT login: {text[:200]}"


def codex_models() -> list[str]:
    cache = read_json(Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "models_cache.json", {}) or {}
    return [m.get("slug") for m in cache.get("models", []) if isinstance(m, dict) and m.get("slug")]


# --- planner --------------------------------------------------------------------------------

def _planner_prompt(phase: str, request: dict, manifest_text: str) -> str:
    transcript = "\n\n".join(f"### {t['role'].upper()}\n{t['content']}" for t in request["planning_transcript"])
    return (
        f"{PLANNER_ROLE}\n\n{PHASE_INSTRUCTIONS[phase]}\n\n"
        f"PROJECT: {request.get('project') or '(none — not code-bound or not yet chosen)'}\n"
        + (f"ALSO IN THIS WORKSPACE: {', '.join(request.get('extra_dirs') or [])}\n" if request.get('extra_dirs') else "")
        + "\n"
        f"ORIGINAL USER REQUEST (verbatim):\n{request['original_prompt']}\n\n"
        f"{manifest_text}\n\n"
        f"CONVERSATION SO FAR:\n{transcript or '(none)'}\n"
    )


def _json_lines(on_event):
    """stdout line -> parsed JSON event -> on_event (ignores non-JSON lines)."""
    def on_line(line):
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            return
        if isinstance(ev, dict):
            on_event(ev)
    return on_line


def planner_config(cfg: dict) -> dict:
    """router.yaml's planner, moved to the other CLI when its own agent is switched off: a Claude-only
    install plans with Claude, a Codex-only one with Codex (planner.claude_model / planner.codex_model)."""
    from .common import agents
    pcfg = dict(cfg.get("planner", {}) or {})
    on = agents().enabled()
    if pcfg.get("provider", "codex-cli") == "codex-cli" and "codex" not in on:
        pcfg.update(provider="claude-cli", model=pcfg.get("claude_model") or None)
    elif pcfg.get("provider") == "claude-cli" and "claude" not in on:
        pcfg.update(provider="codex-cli", model=pcfg.get("codex_model") or "gpt-6-astra")
    return pcfg


def run_planner(phase: str, request: dict, manifest_text: str, images: list[str], cfg: dict,
                work_dir: Path, on_event=None) -> PlanResult:
    pcfg = planner_config(cfg)
    provider = pcfg.get("provider", "codex-cli")
    model = pcfg.get("model")
    timeout = int(pcfg.get("timeout_seconds", 900))
    prompt = _planner_prompt(phase, request, manifest_text)
    cwd = request.get("project") or str(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    if provider == "codex-cli":
        ok, why = codex_auth()
        if not ok:
            return PlanResult(False, None, provider, model, f"planner auth check failed: {why}")
        available = codex_models()
        if model not in available:
            return PlanResult(False, None, provider, model,
                              f"planner model '{model}' is not exposed to this Codex login "
                              f"(available: {', '.join(available) or 'none'}). Not substituting another model.")
        schema_file = work_dir / "plan.schema.json"
        schema_file.write_text(json.dumps(PLAN_SCHEMA))
        out_file = work_dir / f"plan-{phase}-{int(time.time())}.json"
        cmd = ["codex", "exec", "--skip-git-repo-check", "-m", model, "-s", "read-only", "-C", cwd,
               "--output-schema", str(schema_file), "-o", str(out_file),
               "-c", f'model_reasoning_effort="{pcfg.get("reasoning_effort", "high")}"']
        if on_event:
            cmd.append("--json")
        for img in images:
            cmd += ["-i", img]
        cmd.append("-")
        if on_event:
            code, out, err = _run_stream(cmd, cwd=cwd, env=_clean_env(_OPENAI_BILLING_VARS), timeout=timeout,
                                         stdin=prompt, on_line=_json_lines(on_event))
        else:
            code, out, err = _run(cmd, cwd=cwd, env=_clean_env(_OPENAI_BILLING_VARS), timeout=timeout, stdin=prompt)
        raw = out_file.read_text() if out_file.exists() else ""
        if code != 0 or not raw:
            return PlanResult(False, None, provider, model, f"codex exec exit {code}: {err[-600:]}", raw)
        try:
            return PlanResult(True, json.loads(raw), provider, model, raw=raw)
        except json.JSONDecodeError as exc:
            return PlanResult(False, None, provider, model, f"planner returned invalid JSON: {exc}", raw)

    if provider == "claude-cli":
        ok, why = claude_auth()
        if not ok:
            return PlanResult(False, None, provider, model, f"planner auth check failed: {why}")
        cmd = ["claude", "-p", "--output-format", "json", "--permission-mode", "plan",
               "--json-schema", json.dumps(PLAN_SCHEMA)]
        if model:
            cmd += ["--model", model]
        code, out, err = _run(cmd, cwd=cwd, env=_clean_env(_ANTHROPIC_BILLING_VARS), timeout=timeout,
                              stdin=prompt + "\n(Images, if any, are at the listed paths — read them.)")
        try:
            env = json.loads(out)
            data = env.get("structured_output") or json.loads(env.get("result", ""))
            return PlanResult(True, data, provider, model or env.get("model", "claude default"), raw=out)
        except Exception as exc:
            return PlanResult(False, None, provider, model, f"claude planner failed ({code}): {exc} {err[-400:]}", out)

    return PlanResult(False, None, provider, model, f"unknown planner provider '{provider}'")


# --- executor -------------------------------------------------------------------------------

WORKER_RULES = """You are the execution worker of the user's command centre (dispatched by Hermes).
- Do the job directly. No planning ceremony for small tasks.
- Original attachments are listed with absolute paths. Open the ORIGINAL files (Read handles
  images and PDFs). Derived files are supplementary only. Never modify originals.
- This machine is an 8 GB M1: use at most {max_subagents} parallel subagents and only when they
  clearly help; never spawn agents for trivial edits. Reuse an already-running dev server instead of
  starting another; stop any server/browser you started before finishing.
- Specialist tools: Higgsfield via the `higgsfield` CLI / higgsfield-* skills (subscription);
  ElevenLabs via its CLI (`elevenlabs`, OAuth) when audio is needed. Only send an external service
  the specific attachments its job needs. Do not generate paid assets the task did not ask for.
- Subagents get only the context their task needs (relevant requirement, decisions, files,
  acceptance criteria), never the whole conversation.
- Reference files/paths instead of pasting large unchanged content. Do not re-read or re-send
  attachments you already have.
- Be exhaustive in execution, minimal in words. Before each major step, write ONE short status line
  (max 12 words, e.g. "Rendering preview frames to check the chart."); the user sees these live.
  No other narration.
- Verify your work (tests/build/typecheck/visual check as appropriate) before reporting.
- Final message, short bullets only:
    Done: <what changed>
    Verified: <tests/build/status>
    Note: <only if something important remains; name any specialist used, e.g. "Used Higgsfield: hero video">
  On failure: Problem / Cause / Required action. If nothing to add beyond success: "Done."
  Never claim success you did not verify.
""" + QUESTION_RULES.replace("{", "{{").replace("}", "}}")


class WorkerSlot:
    """File-lock concurrency shared by every Hermes process on this machine: one job at a time per
    conversation (its own queue), and at most `max_parallel` jobs across all conversations."""

    def __init__(self, max_parallel: int, wait_seconds: int, session_key: str | None = None):
        self.max = max(1, int(max_parallel))
        self.wait = wait_seconds
        self.key = "".join(c if c.isalnum() else "-" for c in (session_key or ""))[:80]
        self.fh = self.session_fh = None

    def _take(self, path: Path, deadline: float):
        while True:
            fh = open(path, "w")
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fh
            except BlockingIOError:
                fh.close()
            if time.time() > deadline:
                return None
            time.sleep(3)

    def __enter__(self):
        LOCKS_DIR.mkdir(parents=True, exist_ok=True)
        deadline = time.time() + self.wait
        if self.key:  # this conversation's own queue
            self.session_fh = self._take(LOCKS_DIR / f"claude-session-{self.key}.lock", deadline)
            if self.session_fh is None:
                raise TimeoutError("this conversation is still running an earlier Claude job")
        while True:  # machine-wide cap
            for i in range(self.max):
                fh = open(LOCKS_DIR / f"claude-worker-{i}.lock", "w")
                try:
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self.fh = fh
                    return self
                except BlockingIOError:
                    fh.close()
            if time.time() > deadline:
                self.__exit__()
                raise TimeoutError(f"all {self.max} Claude worker slots busy")
            time.sleep(3)

    def __exit__(self, *exc):
        for fh in (self.fh, self.session_fh):
            if fh:
                fcntl.flock(fh, fcntl.LOCK_UN)
                fh.close()
        self.fh = self.session_fh = None


def build_claude_cmd(ecfg: dict, *, add_dirs: list[str], resume_session: str | None, stream: bool,
                     model: str | None = None, effort: str | None = None) -> list[str]:
    """`claude -p` argv. model/effort (the conversation's picker choice) win over executor.model."""
    from .executor_choice import claude_args
    fmt = ["stream-json", "--verbose"] if stream else ["json"]
    cmd = ["claude", "-p", "--output-format", *fmt,
           "--permission-mode", str(ecfg.get("permission_mode", "auto")),
           "--append-system-prompt", WORKER_RULES.format(max_subagents=ecfg.get("max_subagents", 2))]
    # Shared memory changes only through hmem proposals; file tools can't edit the store directly (Bash
    # edits are caught by hmem's integrity check: uncommitted memory files are ignored).
    cmd += ["--disallowedTools", *[f"{t}(~/hermes/memory/**)" for t in ("Edit", "Write", "MultiEdit", "NotebookEdit")],
            "Read(~/.hermes/webui/sidecar-auth/**)"]  # sidecar token: a worker must not drive the Projects API
    for d in add_dirs:
        cmd += ["--add-dir", d]
    cmd += claude_args(model or (str(ecfg["model"]) if ecfg.get("model") else None), effort)
    if resume_session:
        cmd += ["--resume", resume_session]
    return cmd


def run_claude(prompt: str, *, cwd: str, add_dirs: list[str], cfg: dict, resume_session: str | None,
               on_progress=None, session_key: str | None = None, on_event=None,
               model: str | None = None, effort: str | None = None) -> dict:
    """Run Claude Code headless. With on_progress, streams (stream-json) and calls on_progress(line)
    for each step (thinking, text, tool calls, errors) as it happens."""
    ecfg = cfg.get("executor", {}) or {}
    ok, why = claude_auth()
    if not ok:
        return {"ok": False, "error": f"Claude auth check failed: {why}", "billing": None}
    if on_event and not on_progress:
        on_progress = lambda _t: None  # noqa: E731 - events need the stream format
    cmd = build_claude_cmd(ecfg, add_dirs=add_dirs, resume_session=resume_session, stream=bool(on_progress),
                           model=model, effort=effort)
    started = time.time()
    try:
        with WorkerSlot(ecfg.get("max_parallel", 1), int(ecfg.get("queue_wait_seconds", 1800)), session_key):
            run_env, run_timeout = _clean_env(_ANTHROPIC_BILLING_VARS), int(ecfg.get("timeout_seconds", 3600))
            if on_progress:
                import threading
                last = {"at": time.time(), "step": "the task"}
                stop = threading.Event()

                def on_line(line):
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        return
                    if on_event:
                        try:
                            on_event(ev)
                        except Exception:
                            pass  # agent tracking must never fail the run
                    msg = describe_claude_event(ev)
                    if msg:
                        last["at"], last["step"] = time.time(), msg.splitlines()[-1]
                        on_progress(msg)

                def heartbeat():  # sign of life when a step runs long with no new output
                    while not stop.wait(15):
                        if time.time() - last["at"] >= 60:
                            last["at"] = time.time()
                            on_progress(f"Still working: {_short(last['step'], 100)}")
                threading.Thread(target=heartbeat, daemon=True).start()
                try:
                    code, out, err = _run_stream(cmd, cwd=cwd, env=run_env, timeout=run_timeout, stdin=prompt,
                                                 on_line=on_line)
                finally:
                    stop.set()
            else:
                code, out, err = _run(cmd, cwd=cwd, env=run_env, timeout=run_timeout, stdin=prompt)
    except TimeoutError as exc:
        return {"ok": False, "error": str(exc), "billing": why}
    env = None
    for line in reversed(out.strip().splitlines()):  # json: one object; stream-json: last is the result
        try:
            ev = json.loads(line) if on_progress else json.loads(out)
        except json.JSONDecodeError:
            continue
        if not on_progress or ev.get("type") == "result":
            env = ev
            break
    if env is None:
        return {"ok": False, "error": f"claude exit {code}: {(err or out)[-800:]}", "billing": why}
    return {
        "ok": code == 0 and not env.get("is_error"),
        "result": env.get("result", ""),
        "session_id": env.get("session_id"),
        "num_turns": env.get("num_turns"),
        "duration_s": round(time.time() - started, 1),
        "error": None if code == 0 and not env.get("is_error") else (env.get("result") or err[-800:]),
        "billing": why,
        "permission_denials": env.get("permission_denials") or [],
    }


# --- usage limits + Astra takeover executor --------------------------------------------------

import re as _re

_LIMIT_RX = _re.compile(r"usage limit|limit reached|hit your (usage )?limit|rate[_ ]limit|"
                        r"out of (extra )?usage|5-hour limit|weekly limit|quota exceeded", _re.I)


def usage_limit_info(res: dict) -> str | None:
    """If a Claude run failed on a subscription usage limit, return the limit message (incl. reset time)."""
    if res.get("ok"):
        return None
    text = f"{res.get('error') or ''} {res.get('result') or ''}"
    m = _LIMIT_RX.search(text)
    return text.strip()[:300] if m else None


def claude_transcript_tail(cwd: str, session_id: str | None, max_chars: int = 8000) -> str:
    """Compact tail of a Claude Code session: assistant text + tool calls, newest last."""
    if not session_id:
        return ""
    enc = _re.sub(r"[^A-Za-z0-9]", "-", str(Path(cwd).resolve()))
    f = Path.home() / ".claude" / "projects" / enc / f"{session_id}.jsonl"
    if not f.exists():
        return ""
    lines = []
    for raw in f.read_text(errors="replace").splitlines()[-300:]:
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            continue
        msg = ev.get("message") or {}
        if ev.get("type") != "assistant" or not isinstance(msg.get("content"), list):
            continue
        for block in msg["content"]:
            if block.get("type") == "text" and block.get("text", "").strip():
                lines.append("CLAUDE: " + block["text"].strip()[:600])
            elif block.get("type") == "tool_use":
                inp = json.dumps(block.get("input", {}))[:200]
                lines.append(f"TOOL {block.get('name')}: {inp}")
    out = "\n".join(lines)
    return out[-max_chars:]


def git_state(cwd: str) -> str:
    try:
        st = subprocess.run(["git", "status", "--short"], cwd=cwd, capture_output=True, text=True, timeout=20)
        diff = subprocess.run(["git", "diff", "--stat"], cwd=cwd, capture_output=True, text=True, timeout=20)
        return (st.stdout.strip()[:3000] + "\n" + diff.stdout.strip()[:2000]).strip() or "(clean)"
    except Exception:
        return "(not a git repo)"


def build_codex_exec_cmd(*, model: str, effort: str | None, out_file: Path, cwd: str, add_dirs: list[str],
                         resume_thread: str | None = None) -> list[str]:
    """`codex exec` argv for a workspace-write executor run (new thread or `exec resume`)."""
    from .executor_choice import codex_effort_args
    common = ["--skip-git-repo-check", "--json", "-m", model, "-o", str(out_file),
              *codex_effort_args(effort), "-c", "sandbox_workspace_write.network_access=true"]
    if resume_thread:  # `exec resume` takes no -s/-C/--add-dir: same settings via config overrides
        cmd = ["codex", "exec", "resume", resume_thread, *common, "-c", 'sandbox_mode="workspace-write"']
        if add_dirs:
            cmd += ["-c", "sandbox_workspace_write.writable_roots=" + json.dumps(list(add_dirs))]
    else:
        cmd = ["codex", "exec", *common, "-s", "workspace-write", "-C", cwd]
        for d in add_dirs:
            cmd += ["--add-dir", d]
    return cmd + ["-"]


def run_astra_executor(prompt: str, *, cwd: str, add_dirs: list[str], cfg: dict, pid_file: Path,
                       on_event=None, resume_thread: str | None = None,
                       model: str | None = None, effort: str | None = None) -> dict:
    """Codex executing work (ChatGPT subscription): Astra takeover when Claude is unavailable, or the
    OpenAI model the user picked for this conversation (model/effort given; effort None = Codex default).
    resume_thread continues an earlier run (e.g. after the user answered its questions)."""
    pcfg = cfg.get("planner", {}) or {}
    if not model:  # takeover: the configured Astra model at the planner's effort
        model = (cfg.get("takeover", {}) or {}).get("model") or pcfg.get("model")
        effort = pcfg.get("reasoning_effort", "high")
    ok, why = codex_auth()
    if not ok:
        return {"ok": False, "error": f"Codex auth check failed: {why}", "billing": None}
    if model not in codex_models():
        return {"ok": False, "error": f"model '{model}' not exposed to this Codex login", "billing": why}
    out_file = pid_file.with_suffix(".last.txt")
    out_file.unlink(missing_ok=True)
    cmd = build_codex_exec_cmd(model=model, effort=effort, out_file=out_file, cwd=cwd, add_dirs=add_dirs,
                               resume_thread=resume_thread)
    started = time.time()
    thread = {"id": resume_thread}

    def on_ev(ev):
        if ev.get("type") == "thread.started" and ev.get("thread_id"):
            thread["id"] = ev["thread_id"]
        if on_event:
            try:
                on_event(ev)
            except Exception:
                pass

    def on_start(proc):
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(str(proc.pid))
    try:
        code, _out, err = _run_stream(cmd, cwd=cwd, env=_clean_env(_OPENAI_BILLING_VARS),
                                      timeout=int((cfg.get("executor", {}) or {}).get("timeout_seconds", 3600)),
                                      stdin=prompt, on_line=_json_lines(on_ev), on_start=on_start)
    finally:
        pid_file.unlink(missing_ok=True)
    result = out_file.read_text() if out_file.exists() else ""
    stopped = code in (-signal.SIGTERM, -signal.SIGKILL, 143, 137)
    return {"ok": code == 0 and bool(result), "result": result, "thread_id": thread["id"],
            "error": "stopped by /takeover" if stopped else (None if code == 0 else (err or "")[-800:]),
            "billing": why, "model": model, "duration_s": round(time.time() - started, 1)}
