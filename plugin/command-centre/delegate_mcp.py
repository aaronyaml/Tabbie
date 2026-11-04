#!/usr/bin/env python3
"""Tabbie's `tabbie` MCP server: one tool, `delegate`, for dynamic agents.

Attached to a Claude Code worker (`--mcp-config`) or a Codex worker (`-c mcp_servers.tabbie.*`) only
when hand-offs are allowed (providers.dynamic_gate). The worker decides, by its own judgement, whether
to hand a self-contained part of its job to the OTHER provider; this server then runs the other CLI on
the user's subscription (never an API key: providers._clean_env strips them), in the same project
folders, and returns its result.

  Claude worker -> delegate -> `codex exec --ephemeral` (same-tier GPT model, read-only or workspace-write)
  Codex worker  -> delegate -> `claude -p --no-session-persistence` (same-tier Claude model, lean profile)

Guards, in order: depth limit 1 (a delegate never gets this tool), the Hand-offs setting and both agents
signed in, the limits gate (purpose=delegate: Codex stops early to keep the hub's shared pool), one
hand-off at a time per worker, and a machine-wide worker slot (waits at most SLOT_WAIT_S). A refusal is
returned as text so the caller does that part itself; it is also appended to requests/<id>/notes.jsonl,
which the reply relay adds as one Note line.

The context bundle is built here, not by the model: DELEGATE_RULES, the project folders, the user's
original request (verbatim, capped), then the caller's task / why / context / files / expect, and for
write mode the repo state. The delegate's live rows go to requests/<id>/agents-<did>.json (parent = the
calling worker's agent id) so the Agents panel shows the hand-off nested under the worker.

Environment (set by providers.delegate_env): TABBIE_REQUEST_DIR, TABBIE_PARENT_AGENT, TABBIE_DELEGATE_FROM
(claude|codex), TABBIE_CWD, TABBIE_ADD_DIRS (JSON), TABBIE_PARENT_MODEL, TABBIE_PARENT_EFFORT,
TABBIE_SESSION_ID.

Protocol: MCP over stdio, newline-delimited JSON-RPC 2.0 (initialize, tools/list, tools/call, ping).
Stdlib only.
"""

from __future__ import annotations

import importlib
import json
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

PKG = Path(__file__).resolve().parent
if "cc_delegate_pkg" not in sys.modules:
    _pkg = types.ModuleType("cc_delegate_pkg")
    _pkg.__path__ = [str(PKG)]  # submodules without running the plugin's __init__ (Hermes-only)
    sys.modules["cc_delegate_pkg"] = _pkg
providers = importlib.import_module("cc_delegate_pkg.providers")
procutil = importlib.import_module("cc_delegate_pkg.procutil").procutil
agents_mod = importlib.import_module("cc_delegate_pkg.agents")
common = importlib.import_module("cc_delegate_pkg.common")
limits_gate = importlib.import_module("cc_delegate_pkg.limits_gate")
executor_choice = importlib.import_module("cc_delegate_pkg.executor_choice")

SLOT_WAIT_S = 120
CAP_ORIGINAL, CAP_TASK, CAP_CONTEXT, CAP_EXPECT, MAX_FILES, CAP_RESULT = 2000, 6000, 8000, 1500, 30, 12000
LABEL = {"claude": "Claude Code", "codex": "Codex"}

DELEGATE_RULES = """You are a DELEGATE. Another agent ({caller}) is doing a job for the user and handed you ONE
self-contained part of it. Do only that part, well.
- Never ask the user anything. If something blocks you, stop and list the open questions in your final
  message; the caller decides.
- mode=read: do not modify any file. mode=write: change only what the task needs; the caller will not
  touch those files until you return.
- Never git commit, push or stage anything: the caller owns the commits (and runs the tests).
- Reference paths instead of pasting large unchanged content.
- Final message, short: what you did or found, files changed (paths), anything the caller must check."""

TOOL = {
    "name": "delegate",
    "description": (
        "Hand ONE self-contained part of your job to the other coding agent (Claude Code <-> Codex), on the "
        "user's subscription, in the same project folders. Use only when you judge it will clearly do that part "
        "better, or an independent implementation or review helps; never for trivial steps or the whole request. "
        "Blocks until it finishes and returns its final message, the files it changed and its usage. It sees "
        "nothing of your conversation except what you put in task/context; it is given the user's original "
        "request and the folders."),
    "inputSchema": {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "The goal and acceptance criteria, as for a colleague who has seen nothing."},
            "why": {"type": "string", "description": "Why the other agent (one line)."},
            "context": {"type": "string", "description": "Decisions already made, constraints, findings so far (max 8000 chars)."},
            "files": {"type": "array", "items": {"type": "string"}, "description": "Relevant file paths (max 30)."},
            "mode": {"type": "string", "enum": ["read", "write"], "description": "read = no file changes; write = it may edit files you won't touch until it returns."},
            "expect": {"type": "string", "description": "What to return (format, length)."},
        },
        "required": ["task", "why", "mode"],
    },
}


def _peer_label(target, name):
    """'Codex GPT-6 Astra' / 'Claude Opus 5.5', without doubling a brand the model name already has."""
    brand = 'Codex' if target == 'codex' else 'Claude'
    return name if brand.lower() in name.lower() else f"{brand} {name}"


def _cap(s, n: int) -> str:
    s = str(s or "")
    return s if len(s) <= n else s[: n - 1] + "…"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _note(rdir: Path | None, **fields) -> None:
    if not rdir:
        return
    try:
        with (rdir / "notes.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"at": time.time(), **fields}) + "\n")
    except OSError:
        pass


def _changed_files(cwd: str) -> set[str]:
    try:
        out = subprocess.run(["git", "status", "--porcelain"], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
        return {ln[3:].strip() for ln in out.stdout.splitlines() if ln.strip()} if out.returncode == 0 else set()
    except Exception:
        return set()


def bundle(args: dict, *, caller: str, rdir: Path | None, cwd: str, add_dirs: list[str], mode: str) -> str:
    """The delegate's whole prompt. Built here so the caller's model can't drop the essentials."""
    req = common.read_json(rdir / "request.json", {}) if rdir else {}
    original = (req or {}).get("original_prompt") or ""
    files = [str(f) for f in (args.get("files") or []) if str(f).strip()][:MAX_FILES]
    parts = [DELEGATE_RULES.format(caller=caller), "",
             f"WORKING DIRECTORY / PROJECT: {cwd}"
             + (f"\nALSO PART OF THIS WORKSPACE: {', '.join(add_dirs)}" if add_dirs else ""),
             f"MODE: {mode}"]
    if original:
        parts += ["", "THE USER'S ORIGINAL REQUEST (verbatim, for background; do only the task below):",
                  _cap(original, CAP_ORIGINAL)]
    parts += ["", f"TASK (from {caller}):", _cap(args.get("task"), CAP_TASK)]
    if args.get("why"):
        parts += ["", f"WHY YOU: {_cap(args['why'], 300)}"]
    if args.get("context"):
        parts += ["", f"CONTEXT FROM {caller.upper()}:", _cap(args["context"], CAP_CONTEXT)]
    if files:
        parts += ["", "FILES:"] + [f"- {f}" for f in files]
    if args.get("expect"):
        parts += ["", "RETURN:", _cap(args["expect"], CAP_EXPECT)]
    if mode == "write":
        parts += ["", f"REPO STATE ({cwd}):", providers.git_state(cwd)]
    return "\n".join(parts)


class Delegator:
    """One `delegate` call at a time for the worker this server belongs to."""

    def __init__(self, runner=None):
        self.busy = threading.Lock()
        self.procs: set = set()
        self.runner = runner or self._run

    # -- the child CLI -------------------------------------------------------------------------
    def _run(self, cmd, *, cwd, env, timeout, stdin, on_line):
        return providers._run_stream(cmd, cwd=cwd, env=env, timeout=timeout, stdin=stdin, on_line=on_line,
                                     on_start=self.procs.add)

    def kill_all(self) -> None:
        for p in list(self.procs):
            procutil.stop(p.pid)

    # -- the tool ------------------------------------------------------------------------------
    def call(self, args: dict) -> tuple[bool, str]:
        rdir = Path(_env("TABBIE_REQUEST_DIR")) if _env("TABBIE_REQUEST_DIR") else None
        caller = _env("TABBIE_DELEGATE_FROM", "claude")
        target = "codex" if caller == "claude" else "claude"
        who = LABEL[target]
        mode = "write" if str(args.get("mode") or "read").lower() == "write" else "read"
        task = _cap(" ".join(str(args.get("task") or "").split()), 80)

        def refuse(reason: str, note: str) -> tuple[bool, str]:
            _note(rdir, kind="delegate_refused", to=target, reason=reason, note=note, task=task)
            return False, f"{who} unavailable: {note}. Do this part yourself, and add one Note line saying so."

        if not str(args.get("task") or "").strip():
            return False, "delegate needs a task."
        if int(_env("TABBIE_DELEGATE_DEPTH", "0") or 0) >= 1:
            return refuse("depth", "a delegate cannot hand work off again")
        ok, why = providers.dynamic_gate()
        if not ok:
            return refuse("gate", why)
        try:
            cfg = common.load_config()
        except Exception:
            cfg = {}
        ecfg = cfg.get("executor", {}) or {}
        dcfg = cfg.get("dynamic_agents", {}) or {}
        parent_provider = "anthropic" if caller == "claude" else "openai"
        parent_model = _env("TABBIE_PARENT_MODEL") or executor_choice.DEFAULT["model"]
        if parent_provider == "openai" and not parent_model.startswith("gpt"):
            parent_model = "gpt-6-astra"
        try:
            available = providers.codex_models()
        except Exception:
            available = []
        peer = executor_choice.peer_model(parent_provider, parent_model, dcfg.get("models") or {}, available)
        model, effort = peer["model"], (_env("TABBIE_PARENT_EFFORT") or None)
        gate = limits_gate.check(target, model, purpose="delegate", cfg=cfg)
        if gate.blocked:
            return refuse("limit", gate.message)
        if not self.busy.acquire(blocking=False):
            return False, "One hand-off at a time: wait for the running one, or do this part yourself."
        try:
            try:
                slot = providers.WorkerSlot(ecfg.get("max_parallel", 1), SLOT_WAIT_S, None)
                slot.__enter__()
            except TimeoutError:
                return refuse("busy", "every worker slot on this computer is busy")
            try:
                return self._delegate(args, rdir=rdir, caller=caller, target=target, mode=mode, model=model,
                                      effort=effort, peer=peer, ecfg=ecfg, task=task)
            finally:
                slot.__exit__()
        finally:
            self.busy.release()

    def _delegate(self, args, *, rdir, caller, target, mode, model, effort, peer, ecfg, task):
        cwd = _env("TABBIE_CWD") or os.getcwd()
        try:
            add_dirs = [d for d in json.loads(_env("TABBIE_ADD_DIRS", "[]")) if d]
        except json.JSONDecodeError:
            add_dirs = []
        did = time.strftime("%H%M%S-") + secrets.token_hex(2)
        ddir = (rdir / "delegates" / did) if rdir else Path(cwd) / ".tabbie-delegate" / did
        ddir.mkdir(parents=True, exist_ok=True)
        prompt = bundle(args, caller=LABEL[caller], rdir=rdir, cwd=cwd, add_dirs=add_dirs, mode=mode)
        (ddir / "prompt.md").write_text(prompt, encoding="utf-8")
        before = _changed_files(cwd) if mode == "write" else set()

        run = agents_mod.AgentRun(rdir.name if rdir else did, _env("TABBIE_SESSION_ID"))
        run.file_name, run.index = f"agents-{did}.json", False
        run.attach_roots = [cwd, *add_dirs] + ([str(rdir)] if rdir else [])
        row = run.start_main(f"{_peer_label(target, peer['name'])} · {task}",
                             "astra" if target == "codex" else "claude",
                             description=f"hand-off from {LABEL[caller]} ({mode})", model=model)
        row["id"], row["parent"], row["kind"] = f"delegate-{did}", _env("TABBIE_PARENT_AGENT") or None, "delegate"
        row["to"] = target
        run._main = row["id"]
        if rdir:
            run.save(force=True)

        def on_line(line):
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                return
            if not isinstance(ev, dict):
                return
            with run.lock:
                changed = run.apply_claude(ev) if target == "claude" else run.apply_codex(ev)
                if changed and rdir:
                    run.save()

        timeout = providers.run_limit(ecfg)
        out_file = ddir / "last.txt"
        if target == "codex":
            cmd = providers.build_codex_exec_cmd(model=model, effort=effort, out_file=out_file, cwd=cwd,
                                                 add_dirs=add_dirs if mode == "write" else [],
                                                 sandbox="workspace-write" if mode == "write" else "read-only",
                                                 ephemeral=True)
            env = providers._clean_env(providers._OPENAI_BILLING_VARS)
        else:
            from importlib import import_module
            wp = import_module("cc_delegate_pkg.worker_profile")
            empty = wp.mcp_config(ddir / "mcp-empty.json")
            cmd = providers.build_claude_cmd(
                ecfg, add_dirs=add_dirs, resume_session=None, stream=True, model=model, effort=effort,
                profile=ecfg.get("worker_profile", "lean"), mcp_config=empty, dynamic=False,
                extra_disallowed=["Edit", "Write", "MultiEdit", "NotebookEdit"] if mode == "read" else None)
            cmd.append("--no-session-persistence")
            env = providers._clean_env(providers._ANTHROPIC_BILLING_VARS)
            env["TABBIE_WORKER_MODEL"] = model
        env["TABBIE_DELEGATE_DEPTH"] = "1"
        started = time.time()
        try:
            code, out, err = self.runner(cmd, cwd=cwd, env=env, timeout=timeout, stdin=prompt, on_line=on_line)
        except Exception as exc:
            code, out, err = 1, "", repr(exc)
        dur = round(time.time() - started, 1)
        text, tokens = "", 0
        if target == "codex":
            text = out_file.read_text(encoding="utf-8") if out_file.exists() else ""
            for ln in out.splitlines():
                try:
                    ev = json.loads(ln)
                except json.JSONDecodeError:
                    continue
                if isinstance(ev, dict) and ev.get("type") == "turn.completed":
                    u = ev.get("usage") or {}
                    tokens += int(u.get("input_tokens") or 0) + int(u.get("output_tokens") or 0)
        else:
            for ln in reversed(out.strip().splitlines()):
                try:
                    ev = json.loads(ln)
                except json.JSONDecodeError:
                    continue
                if isinstance(ev, dict) and ev.get("type") == "result":
                    text = ev.get("result") or ""
                    u = ev.get("usage") or {}
                    tokens = sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens",
                                                               "cache_read_input_tokens", "output_tokens"))
                    if ev.get("is_error"):
                        code = code or 1
                    break
        ok = code == 0 and bool(text.strip())
        if not ok and rdir and (rdir / providers.STOP_MARKER).exists():
            text = text or "stopped by the user"
        with run.lock:
            run.finish_main(ok, step=None if ok else _cap(err or text or "failed", 140))
            row["tokens"] = row.get("tokens") or tokens
            if rdir:
                run.save(force=True)
        (ddir / "result.md").write_text(text or (err or "")[-4000:], encoding="utf-8")
        changed = sorted(_changed_files(cwd) - before) if mode == "write" else []
        if not ok:
            limit = providers.usage_limit_info({"ok": False, "error": err, "result": text})
            _note(rdir, kind="delegate_failed", to=target, task=task,
                  note=f"{LABEL[target]} hand-off failed" + (" (usage limit)" if limit else ""))
            return False, (f"{LABEL[target]} ({peer['name']}) failed after {dur}s"
                           + (f": {limit}" if limit else f": {_cap((err or text).strip(), 600)}")
                           + ". Do this part yourself, and add one Note line saying so.")
        head = (f"Result from {LABEL[target]} {peer['name']} ({mode}, {dur}s"
                + (f", {round(tokens / 1000, 1)}k tokens" if tokens else "") + "):")
        body = _cap(text.strip(), CAP_RESULT)
        tail = []
        if len(text) > CAP_RESULT:
            tail.append(f"(truncated; full result: {ddir / 'result.md'})")
        if mode == "write":
            tail.append("Files changed: " + (", ".join(changed[:40]) if changed else "none reported by git"))
        return True, "\n".join([head, body, *tail])


# --------------------------------------------------------------------------- MCP stdio loop

def serve(stdin=None, stdout=None, delegator: Delegator | None = None) -> None:
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    d = delegator or Delegator()
    wlock = threading.Lock()
    rdir = Path(_env("TABBIE_REQUEST_DIR")) if _env("TABBIE_REQUEST_DIR") else None
    done = threading.Event()

    def send(msg: dict) -> None:
        with wlock:
            stdout.write(json.dumps(msg) + "\n")
            stdout.flush()

    def watch_stop():  # the user pressed Stop on the Agents block: the hand-off stops too
        while not done.wait(1.0):
            if rdir and (rdir / providers.STOP_MARKER).exists():
                d.kill_all()
    threading.Thread(target=watch_stop, daemon=True).start()

    def do_call(mid, params):
        args = params.get("arguments") or {}
        try:
            ok, text = d.call(args) if params.get("name") == "delegate" else (False, f"unknown tool {params.get('name')}")
        except Exception as exc:  # never crash the server: the caller just does the part itself
            ok, text = False, f"delegate failed ({type(exc).__name__}). Do this part yourself."
        send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": text}], "isError": not ok}})

    for raw in stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue
        mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
        if mid is None:
            continue  # notifications (initialized, cancelled) need no answer
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": params.get("protocolVersion") or "2025-06-18",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "tabbie", "version": "1.0.0"}}})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [TOOL]}})
        elif method == "tools/call":
            threading.Thread(target=do_call, args=(mid, params), daemon=True).start()
        elif method == "ping":
            send({"jsonrpc": "2.0", "id": mid, "result": {}})
        else:
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"unknown method {method}"}})
    done.set()
    d.kill_all()  # the worker went away (stdin closed): no orphaned hand-off


if __name__ == "__main__":
    serve()
