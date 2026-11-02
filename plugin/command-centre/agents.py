"""Per-request agent records: the main worker plus every subagent it spawns.

Fed from Claude Code stream-json events (main worker, Agent/Task subagents via parent_tool_use_id and
system task_* events) and Codex `--json` events (Astra). Persisted per request as
state/requests/<id>/agents.json, with a per-session index at state/agents/<session>.json that the
WebUI sidecar serves.

Record: {id, parent, name, description, provider, model, status (running|done|failed|waiting|stopped),
step, started, ended, duration_s, tokens, output_tokens, tool_uses, kind}
kind: None for a main worker, "subagent" (Claude Agent/Task with task_type local_agent, Codex
spawn_agent), "delegate" (a dynamic-agent hand-off to the other provider, written by delegate_mcp.py
into its own file agents-<id>.json and merged at read time) or "background" (a background shell task:
shown muted, never counted as an agent).
`tokens` follows Claude Code's own subagent figure (totalTokens): the agent's context at its latest
call (input + cache write + cache read + output). `output_tokens` is cumulative.

Also per request (shown live in the reply, and after a reload, by cc-narration.js):
- narration: the short text a main worker writes between tool calls ("Found it, now fixing the CSS."),
  [{at, agent, text, acts}], newest last, capped, secrets scrubbed, code blocks dropped. The text after
  the worker's last tool call is its final answer (already the reply), so it is dropped at the end.
- images: pictures the worker attached mid-run with an `ATTACH: <path>` line (see providers.WORKER_RULES):
  validated (inside the run's folders, png/jpg/webp/gif by content), copied to <request>/shots/<id>.<ext>,
  [{id, name, ext, at, agent, bytes}]. The sidecar serves them (GET /shot).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

from .flockcompat import fcntl
from .common import REQUESTS_DIR, STATE_DIR, read_json, write_json

AGENTS_INDEX_DIR = STATE_DIR / "agents"
_SUB_TOOLS = ("Agent", "Task")

NARRATION_MAX = 80            # timeline entries kept per request (narration lines + operations)
NARRATION_CHARS = 280         # one line, after cleaning
SHOTS_MAX = 24                # images per request
SHOT_MAX_BYTES = 25 * 1024 * 1024
IMAGE_EXT = {".png": "png", ".jpg": "jpg", ".jpeg": "jpg", ".webp": "webp", ".gif": "gif"}
_ATTACH_RX = re.compile(r"^[ \t>*-]*ATTACH(?:\*\*)?:(?:\*\*)?[ \t]*(.+?)[ \t]*$", re.M)
_FENCE_RX = re.compile(r"```.*?(?:```|$)", re.S)
_SHOT_ID = re.compile(r"^[0-9a-f]{16}$")


def _scrub(text: str) -> str:
    """hmem.security.scrub (secrets out of anything the browser sees); hmem lives in the repo root."""
    try:
        from hmem.security import scrub
    except ImportError:
        root = str(Path(__file__).resolve().parents[2])
        if root not in sys.path:
            sys.path.insert(0, root)
        try:
            from hmem.security import scrub
        except ImportError:
            return text
    return scrub(text)


def attach_paths(text: str) -> list[str]:
    """Paths named on `ATTACH: <path>` lines (backticks, quotes and <> around the path are fine)."""
    out = []
    for m in _ATTACH_RX.finditer(text or ""):
        raw = m.group(1).strip().strip("`'\"<>").strip()
        if raw and raw not in out:
            out.append(raw)
    return out


def strip_attach(text: str) -> str:
    """Text without its ATTACH lines (the images show in the reply's Agents block instead)."""
    if not text or "ATTACH" not in text:
        return text
    return re.sub(r"\n{3,}", "\n\n", _ATTACH_RX.sub("", text)).strip()


def clean_narration(text: str) -> str | None:
    """A worker text block -> one short, safe narration line (None = nothing to show)."""
    t = _FENCE_RX.sub(" ", strip_attach(text or ""))
    t = " ".join(t.split())
    if not t:
        return None
    t = _scrub(t)
    return t if len(t) <= NARRATION_CHARS else t[: NARRATION_CHARS - 1].rstrip() + "…"


_PAST = (("Running:", "Ran:"), ("Reading", "Read"), ("Editing", "Edited"), ("Writing", "Wrote"),
         ("Searching the web for", "Searched the web for"), ("Searching the web", "Searched the web"),
         ("Searching for", "Searched for"), ("Finding", "Found"), ("Fetching", "Fetched"),
         ("Delegating:", "Delegated:"), ("Using skill", "Used skill"), ("Using", "Used"),
         ("Updating the task list", "Updated the task list"), ("Updating the plan", "Updated the plan"),
         ("Handing off to", "Handed off to"), ("Starting a sub-agent", "Started a sub-agent"),
         ("Waiting for sub-agents", "Waited for sub-agents"))


def past_tense(label: str) -> str:
    """"Running: npm test" -> "Ran: npm test", "Editing HeroDeck.tsx" -> "Edited HeroDeck.tsx"."""
    for now_, done in _PAST:
        if label.startswith(now_):
            return done + label[len(now_):]
    return label


def _magic_ok(head: bytes, ext: str) -> bool:
    return {"png": head.startswith(b"\x89PNG\r\n\x1a\n"), "jpg": head.startswith(b"\xff\xd8\xff"),
            "gif": head[:6] in (b"GIF87a", b"GIF89a"),
            "webp": head[:4] == b"RIFF" and head[8:12] == b"WEBP"}.get(ext, False)


def check_image(raw: str, roots: list[str]) -> tuple[Path | None, str]:
    """(resolved path, ext) when raw names an existing png/jpg/webp/gif inside one of roots, else
    (None, why not). Relative paths are taken from the first root. Symlinks are resolved first."""
    if not raw or "\x00" in raw:
        return None, "no path"
    if not roots:
        return None, "outside the project folders"
    try:
        p = Path(os.path.expanduser(raw))
        if not p.is_absolute():
            p = Path(roots[0]) / p
        try:
            p = p.resolve(strict=True)
        except OSError:
            return None, "file not found"
        if not p.is_file():
            return None, "file not found"
        ext = IMAGE_EXT.get(p.suffix.lower())
        if not ext:
            return None, "not a png, jpg, webp or gif"
        size = p.stat().st_size
        if size > SHOT_MAX_BYTES:
            return None, "too large"
        inside = False
        for r in roots:
            try:
                rr = Path(r).expanduser().resolve()
            except OSError:
                continue
            if rr != Path(rr.anchor) and os.path.commonpath([str(p), str(rr)]) == str(rr):
                inside = True
                break
        if not inside:
            return None, "outside the project folders"
        with open(p, "rb") as fh:
            if size < 16 or not _magic_ok(fh.read(16), ext):
                return None, "not a valid image"
        return p, ext
    except (OSError, ValueError, RuntimeError):
        return None, "unreadable"


def validate_image(raw: str, roots: list[str]) -> tuple[Path, str] | None:
    p, ext = check_image(raw, roots)
    return (p, ext) if p else None


def backfill_images(request_id: str, text: str, roots: list[str], existing: list[dict] | None = None,
                    now: float | None = None, base: Path | None = None) -> dict:
    """ATTACH lines of a finished reply -> {images, attach_rejected} (stored like a live attach). For replies
    written before live pickup, or whose live pickup missed (the sidecar runs this once per request)."""
    run = AgentRun(request_id, data={"agents": [], "images": [dict(i) for i in existing or []]})
    run.attach_roots, run.base = list(roots), base
    n0 = len(run.data["images"])
    a = {"id": "reply", "parent": None}
    for raw in attach_paths(text):
        run.attach(raw, a, time.time() if now is None else now)
    return {"images": run.data["images"][n0:], "attach_rejected": run.data.get("attach_rejected") or []}


def shot_path(request_id: str, shot_id: str, base: Path | None = None) -> Path | None:
    """The stored copy of an attached image (None for a bad id or no such file)."""
    if not _SHOT_ID.match(shot_id or ""):
        return None
    d = (base or REQUESTS_DIR) / request_id / "shots"
    for ext in ("png", "jpg", "webp", "gif"):
        f = d / f"{shot_id}.{ext}"
        if f.is_file():
            return f
    return None


def _short(s, n: int) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _safe(s: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in (s or "default"))[:120]


def _ctx_tokens(u: dict) -> int:
    return int(u.get("input_tokens") or 0) + int(u.get("cache_creation_input_tokens") or 0) \
        + int(u.get("cache_read_input_tokens") or 0) + int(u.get("output_tokens") or 0)


def new_agent(agent_id: str, name: str, *, parent: str | None = None, provider: str = "claude",
              description: str = "", now: float | None = None) -> dict:
    return {"id": agent_id, "parent": parent, "name": name, "description": description,
            "provider": provider, "model": None, "status": "running", "step": "Starting",
            "started": now if now is not None else time.time(), "ended": None, "duration_s": None,
            "tokens": 0, "output_tokens": 0, "tool_uses": 0, "kind": "subagent" if parent else None}


def _finish(a: dict, status: str, now: float, duration_s: float | None = None) -> None:
    if a["status"] not in ("running", "waiting"):
        return
    a["status"] = status
    a["ended"] = now
    a["duration_s"] = round(duration_s if duration_s is not None else now - a["started"], 1)


def supersede_waiting(agents: list[dict]) -> list[dict]:
    """A run that asked a question is answered once a newer main run exists. Saves from overlapping
    runs can race, so this is applied on every save and read rather than only in start_main()."""
    mains = [a for a in agents if a.get("parent") is None]
    for a in mains[:-1]:
        if a.get("status") == "waiting":
            a["status"], a["step"] = "done", "Answered"
    return agents


def _step_of(block: dict) -> str | None:
    from .providers import describe_claude_event  # one source of truth for step wording
    line = describe_claude_event({"type": "assistant", "message": {"content": [block]}})
    if not line:
        return None
    line = line.splitlines()[-1]
    return "Thinking" if line.startswith("Thinking:") else _short(line, 140)


_SHELL_WRAP = re.compile(r"^(?:/\S*/)?(?:ba|z)?sh\s+-l?c\s+(?P<q>['\"])(?P<cmd>.*)(?P=q)\s*$", re.S)


def _codex_cmd(cmd) -> str:
    """Codex runs everything as `/bin/zsh -lc '<command>'`: show the command itself."""
    s = str(cmd or "").strip()
    m = _SHELL_WRAP.match(s)
    return m.group("cmd").replace("'\\''", "'") if m else s


def _codex_op_label(it: dict) -> str | None:
    k = it.get("type")
    if k == "command_execution":
        return "Running: " + _short(_codex_cmd(it.get("command")), 120)
    if k == "file_change":
        names = [Path(str(c.get("path") or "")).name for c in it.get("changes") or [] if isinstance(c, dict)]
        names = [n for n in names if n]
        if len(names) == 1:
            return "Editing " + _short(names[0], 120)
        return f"Editing {len(names)} files" if names else "Editing files"
    if k == "mcp_tool_call":
        if it.get("server") == "tabbie" and it.get("tool") == "delegate":
            return "Handing off to Claude Code"
        return f"Using {it.get('tool') or 'a tool'}"
    if k == "web_search":
        return "Searching the web" + (f" for {_short(it.get('query'), 100)}" if it.get("query") else "")
    if k == "collab_tool_call":
        return "Starting a sub-agent" if str(it.get("tool") or "").startswith("spawn") else None
    return None


class AgentRun:
    """Agent records for one request. Mutated by apply_* (pure, testable), persisted by save()."""

    def __init__(self, request_id: str, session_id: str = "", data: dict | None = None):
        self.request_id, self.session_id = request_id, session_id
        self.data = data or {"request_id": request_id, "session_id": session_id, "agents": []}
        self._msgs: dict[str, dict] = {}      # message id -> {agent, out}
        self._main: str | None = None
        self._saved = 0.0
        self.file_name = "agents.json"   # a delegate's rows live in agents-<id>.json (no write race)
        self.index = True                 # update the session index on save (main runs only)
        self.attach_roots: list[str] = [] # folders an ATTACH line may name (the run's cwd + extra dirs)
        self.lock = threading.RLock()     # apply_* (stream reader) vs a deferred save (timer)
        self._acts: dict[str, int] = {}   # agent id -> tool calls so far (narration after the last = answer)
        self._said: set = set()           # narration already taken (message id, text)
        self._timer = None
        self.base: Path | None = None     # requests dir (default REQUESTS_DIR; the sidecar passes its own)

    # -- lookup ---------------------------------------------------------------------------
    @property
    def agents(self) -> list[dict]:
        return self.data["agents"]

    def get(self, agent_id: str) -> dict | None:
        return next((a for a in self.agents if a["id"] == agent_id), None)

    def main(self) -> dict | None:
        return self.get(self._main) if self._main else None

    def start_main(self, name: str, provider: str, *, description: str = "", model: str | None = None,
                   now: float | None = None) -> dict:
        for prev in self.agents:  # an answered question: the asking run is finished
            if prev["status"] == "waiting":
                prev["status"], prev["step"] = "done", "Answered"
        n = sum(1 for a in self.agents if a["parent"] is None) + 1
        a = new_agent(f"{provider}-{n}", name, provider=provider, description=description, now=now)
        a["model"] = model
        self.agents.append(a)
        self._main = a["id"]
        return a

    # -- Claude stream-json ---------------------------------------------------------------
    def apply_claude(self, ev: dict, now: float | None = None) -> bool:
        """Apply one stream-json event. Returns True when something visible changed."""
        now = time.time() if now is None else now
        main = self.main() or self.start_main("Claude Code", "claude", now=now)
        t, st = ev.get("type"), ev.get("subtype")
        if t == "system" and st == "init":
            self._reopen(main)
            main["model"] = ev.get("model") or main["model"]
            main["step"] = "Started"
            return True
        if t == "system" and st == "background_tasks_changed":
            # the live background list: a task that left it has ended, even if its notification never came
            live = {x.get("task_id") for x in ev.get("tasks") or [] if isinstance(x, dict)}
            changed = False
            for a in self.agents:
                if a.get("kind") == "background" and a["status"] == "running" and a.get("task_id") \
                        and a["task_id"] not in live:
                    _finish(a, "done", now)
                    a["step"], changed = "Done", True
            return changed
        if t == "system" and st in ("task_started", "task_progress", "task_notification"):
            tid = ev.get("tool_use_id")
            if not tid:
                return False
            existing = self.get(tid)
            if st == "task_started" and ev.get("is_backgrounded") is False and ev.get("task_type") != "local_agent" \
                    and existing is None:
                return False  # a foreground command inside a subagent: not a task of its own
            if existing is None and st != "task_started":
                return False  # progress or the end of a task we skipped (or never saw start)
            background = (ev.get("task_type") not in (None, "local_agent") if st == "task_started"
                          else (existing is None and not ev.get("subagent_type")))
            if existing is None and background:
                # a background shell task (e.g. "sleep 50" in the background): a muted row, not an agent
                a = self._sub(tid, ev.get("description") or "Background task", now)
                a["kind"], a["description"] = "background", "background task"
                a["step"] = "Running in the background"
            else:
                a = self._sub(tid, ev.get("description") or ev.get("subagent_type") or "Subagent", now)
            if ev.get("task_id") and not a.get("task_id"):
                a["task_id"] = str(ev["task_id"])[:80]
            if a.get("kind") == "background":
                if ev.get("description"):
                    a["name"] = _short(ev["description"], 80)
                if st == "task_notification":
                    ok = ev.get("status") in ("completed", "done", "success")
                    _finish(a, "done" if ok else "failed", now)
                    a["step"] = "Done" if ok else "Failed"
                return True
            if st == "task_started" and ev.get("is_backgrounded"):
                a["async"] = True  # launched in the background: runs on after its tool call returns
            if ev.get("subagent_type"):
                a["description"] = ev["subagent_type"]
            if ev.get("description"):
                a["name"] = _short(ev["description"], 80)
            u = ev.get("usage") or {}
            if u.get("total_tokens"):
                a["tokens"] = int(u["total_tokens"])
            if u.get("tool_uses") is not None:
                a["tool_uses"] = int(u["tool_uses"])
            if st == "task_progress" and ev.get("last_tool_name"):
                a["step"] = f"Using {ev['last_tool_name']}"
            if st == "task_notification":
                ok = ev.get("status") in ("completed", "done", "success")
                _finish(a, "done" if ok else "failed", now,
                        (u.get("duration_ms") / 1000) if u.get("duration_ms") else None)
                a["step"] = "Done" if ok else "Failed"
            return True
        if t == "assistant":
            msg = ev.get("message") or {}
            parent = ev.get("parent_tool_use_id")
            if not parent:
                self._reopen(main)
            a = self._sub(parent, "Subagent", now) if parent else main
            if msg.get("model") and not a.get("model"):
                a["model"] = msg["model"]
            u = msg.get("usage") or {}
            mid = msg.get("id") or f"anon-{len(self._msgs)}"
            if u:
                prev = self._msgs.get(mid)
                out = int(u.get("output_tokens") or 0)
                if prev is None:
                    a["output_tokens"] += out
                elif out > prev["out"]:
                    a["output_tokens"] += out - prev["out"]
                self._msgs[mid] = {"agent": a["id"], "out": max(out, prev["out"] if prev else 0)}
                a["tokens"] = max(a["tokens"], _ctx_tokens(u)) if parent else _ctx_tokens(u)
            for b in msg.get("content") or []:
                if not isinstance(b, dict):
                    continue
                step = _step_of(b)
                if step:
                    a["step"] = step
                if b.get("type") == "text" and b.get("text"):
                    self.take_text(a, b["text"], now, key=mid)
                if b.get("type") == "tool_use":
                    self._act(a)
                    self.take_op(a, b.get("id"), b.get("name") or "", step, now)
                    a["tool_uses"] += 1
                    if b.get("name") in _SUB_TOOLS and b.get("id"):
                        i = b.get("input") or {}
                        s = self._sub(b["id"], i.get("description") or i.get("subagent_type") or "Subagent", now)
                        s["description"] = i.get("subagent_type") or s["description"]
            return True
        if t == "user" and not ev.get("parent_tool_use_id"):
            changed = False
            for b in (ev.get("message") or {}).get("content") or []:
                if not isinstance(b, dict) or b.get("type") != "tool_result":
                    continue
                changed = self.end_op(b.get("tool_use_id"), bool(b.get("is_error")), now) or changed
                a = self.get(b.get("tool_use_id") or "")
                if not a or a["parent"] is None or a.get("kind") == "background":
                    continue  # a background task's tool_result is only "running in background with ID ..."
                tur = ev.get("tool_use_result") if isinstance(ev.get("tool_use_result"), dict) else {}
                if tur.get("isAsync") or tur.get("status") == "async_launched":
                    a["async"] = True  # only "Async agent launched": it reports back in a task_notification
                    continue
                if tur.get("totalTokens"):
                    a["tokens"] = int(tur["totalTokens"])
                if tur.get("totalToolUseCount") is not None:
                    a["tool_uses"] = int(tur["totalToolUseCount"])
                if tur.get("resolvedModel"):
                    a["model"] = tur["resolvedModel"]
                failed = bool(b.get("is_error")) or tur.get("status") in ("failed", "error")
                dur = tur.get("totalDurationMs")
                if a["status"] == "running":
                    _finish(a, "failed" if failed else "done", now, dur / 1000 if dur else None)
                elif dur:
                    a["duration_s"] = round(dur / 1000, 1)
                a["step"] = "Failed" if failed else "Done"
                changed = True
            return changed
        if t == "result":
            ok = not ev.get("is_error") and ev.get("subtype", "success") == "success"
            if ok and any((a.get("kind") == "background" or a.get("async")) and a["status"] == "running"
                          for a in self.agents):
                # Claude ends the turn while background tasks run, then wakes when they finish and goes on
                main["step"], main["reopened"] = "Waiting for background tasks", True  # duration: whole run
                return True
            for a in self.agents:
                if a["status"] == "running" and (a["parent"] == main["id"] or a["parent"] == "sub"):
                    _finish(a, "done" if ok else "failed", now)
            _finish(main, "done" if ok else "failed", now,
                    (ev["duration_ms"] / 1000) if ev.get("duration_ms") and not main.get("reopened") else None)
            self.drop_final(main["id"])
            main["step"] = "Done" if ok else _short(ev.get("result") or "Failed", 140)
            if ev.get("num_turns") is not None:
                main["turns"] = ev["num_turns"]
            return True
        return False

    def _reopen(self, main: dict) -> None:
        """Claude can end a turn (a result event) and then carry on in the same process, e.g. woken by a
        background task finishing. Activity after that means the main agent is running again."""
        if main["status"] in ("done", "failed"):
            main["status"], main["ended"], main["duration_s"], main["reopened"] = "running", None, None, True

    # -- narration + attached images -------------------------------------------------------
    def _act(self, a: dict) -> None:
        self._acts[a["id"]] = self._acts.get(a["id"], 0) + 1

    def take_text(self, a: dict, text: str, now: float, key=None) -> bool:
        """A worker's text block: ATTACH lines become images; a main worker's words become narration."""
        changed = False
        for raw in attach_paths(text):
            changed = self.attach(raw, a, now) or changed
        if a.get("parent") is not None or a.get("kind") in ("background", "delegate"):
            return changed
        line = clean_narration(text)
        k = (key, line)
        if not line or k in self._said:
            return changed
        self._said.add(k)
        narr = self.data.setdefault("narration", [])
        narr.append({"at": now, "agent": a["id"], "kind": "text", "text": line, "acts": self._acts.get(a["id"], 0)})
        del narr[:-NARRATION_MAX]
        return True

    def take_op(self, a: dict, op_id, tool: str, label: str | None, now: float) -> bool:
        """A main worker's tool call, in order with its narration: {kind: op, tool, text (live label),
        done (past tense), status running|done|failed}. Labels are the same short step lines the Agents
        block shows (file names, a Bash description), scrubbed; never tool output."""
        if a.get("parent") is not None or a.get("kind") in ("background", "delegate") or not label:
            return False
        label = _scrub(label)
        narr = self.data.setdefault("narration", [])
        narr.append({"at": now, "agent": a["id"], "kind": "op", "id": str(op_id or "")[:80], "tool": tool[:40],
                     "text": label, "done": past_tense(label), "status": "running", "acts": self._acts.get(a["id"], 0)})
        del narr[:-NARRATION_MAX]
        return True

    def end_op(self, op_id, failed: bool, now: float) -> bool:
        if not op_id:
            return False
        for n in reversed(self.data.get("narration") or []):
            if n.get("kind") == "op" and n.get("id") == op_id:
                if n.get("status") == "running":
                    n["status"], n["ended"] = ("failed" if failed else "done"), now
                    return True
                return False
        return False

    def drop_final(self, agent_id: str | None) -> None:
        """The text after an agent's last tool call is its final answer (the reply itself): not narration.
        Operations still running when the run ends are settled with it."""
        if not agent_id:
            return
        narr = self.data.get("narration") or []
        n = self._acts.get(agent_id, 0)
        while narr and narr[-1].get("agent") == agent_id and narr[-1].get("kind", "text") == "text" \
                and narr[-1].get("acts", 0) >= n:
            narr.pop()
        for e in narr:
            if e.get("agent") == agent_id and e.get("status") == "running":
                e["status"] = "done"

    def attach(self, raw: str, a: dict, now: float) -> bool:
        imgs = self.data.setdefault("images", [])
        if len(imgs) >= SHOTS_MAX:
            return False
        p, ext = check_image(raw, self.attach_roots)
        if p is None:  # shown in the reply as "couldn't show": a silent drop looks like a broken chat
            rej = self.data.setdefault("attach_rejected", [])
            name = _short(Path(raw).name or raw, 120)
            if len(rej) < SHOTS_MAX and not any(r.get("name") == name for r in rej):
                rej.append({"name": name, "reason": ext, "at": now})
            return True
        try:
            blob = p.read_bytes()
        except OSError:
            return False
        sid = hashlib.sha1(blob).hexdigest()[:16]
        if any(i.get("id") == sid for i in imgs):
            return False
        dest = (self.base or REQUESTS_DIR) / self.request_id / "shots" / f"{sid}.{ext}"
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                tmp = dest.with_name(f".tmp-{sid}-{os.getpid()}")
                tmp.write_bytes(blob)
                os.replace(tmp, dest)
        except OSError:
            return False
        imgs.append({"id": sid, "name": _short(p.name, 120), "ext": ext, "at": now, "agent": a["id"],
                     "bytes": len(blob)})
        return True

    def _sub(self, tool_use_id: str, name: str, now: float) -> dict:
        a = self.get(tool_use_id)
        if a is None:
            a = new_agent(tool_use_id, _short(name, 80), parent=self._main or "sub", now=now)
            a["description"] = "subagent"
            self.agents.append(a)
        return a

    # -- Codex --json (Astra) -------------------------------------------------------------
    def apply_codex(self, ev: dict, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        main = self.main() or self.start_main("Astra", "astra", now=now)
        t = ev.get("type", "")
        if t == "thread.started":
            main["thread_id"] = ev.get("thread_id")
            main["step"] = "Started"
            return True
        if t in ("item.started", "item.completed", "item.updated"):
            it = ev.get("item") or {}
            k = it.get("type")
            step = {"reasoning": "Thinking", "file_change": "Editing files", "web_search": "Searching the web",
                    "todo_list": "Updating the plan"}.get(k)
            if k in ("command_execution", "file_change", "mcp_tool_call", "web_search", "collab_tool_call"):
                key = ("tool", it.get("id")) if it.get("id") else None  # one call: started + completed
                if (key and key not in self._said) or (not key and t == "item.started"):
                    if key:
                        self._said.add(key)
                    self._act(main)
                    self.take_op(main, it.get("id"), k, _codex_op_label(it), now)
                if t == "item.completed" and it.get("id"):
                    bad = str(it.get("status") or "").lower() in ("failed", "error", "declined") or \
                        (it.get("exit_code") not in (None, 0))
                    self.end_op(it["id"], bad, now)
            if k == "agent_message" and it.get("text"):
                step = _short(it["text"], 140)
                if t == "item.completed":
                    self.take_text(main, it["text"], now, key=it.get("id"))
            elif k == "command_execution":
                step = "Running: " + _short(_codex_cmd(it.get("command")), 120)
                if t == "item.started":
                    main["tool_uses"] += 1
            elif k == "mcp_tool_call":
                step = f"Using {it.get('tool') or 'a tool'}"
                if it.get("server") == "tabbie" and it.get("tool") == "delegate":
                    step = "Handing off to Claude Code"
            elif k == "collab_tool_call":
                return self._codex_collab(main, t, it, now)
            if step:
                main["step"] = step
                return True
            return False
        if t == "turn.completed":
            u = ev.get("usage") or {}
            main["tokens"] += int(u.get("input_tokens") or 0) + int(u.get("output_tokens") or 0)
            main["output_tokens"] += int(u.get("output_tokens") or 0)
            return True
        if t in ("turn.failed", "error"):
            main["step"] = _short((ev.get("error") or {}).get("message") if isinstance(ev.get("error"), dict)
                                  else ev.get("message") or "Failed", 140)
            return True
        return False

    def _codex_collab(self, main: dict, t: str, it: dict, now: float) -> bool:
        """Codex native sub-agents (`collab_tool_call` items: spawn / wait / send / close). Shape from a
        captured fixture (tests/fixtures/codex-collab-wait.jsonl): tool, receiver_thread_ids, prompt,
        agents_states {thread_id: {status}}, status."""
        tool = str(it.get("tool") or "")
        changed = False
        if tool.startswith("spawn"):
            for tid in it.get("receiver_thread_ids") or []:
                if self.get(tid) is None:
                    a = new_agent(tid, _short(it.get("prompt") or "Sub-agent", 80), parent=main["id"],
                                  provider=main.get("provider") or "astra", now=now)
                    a["description"] = "sub-agent"
                    self.agents.append(a)
                    changed = True
            if t == "item.started":
                main["step"] = "Starting a sub-agent"
                changed = True
        states = it.get("agents_states") or {}
        for tid, stt in states.items():
            a = self.get(tid)
            status = str((stt or {}).get("status") if isinstance(stt, dict) else stt or "").lower()
            if a and status in ("completed", "done", "shutdown", "closed", "errored", "failed", "error"):
                _finish(a, "failed" if status in ("errored", "failed", "error") else "done", now)
                a["step"] = "Failed" if a["status"] == "failed" else "Done"
                changed = True
        if tool == "wait" and t == "item.started":
            main["step"] = "Waiting for sub-agents"
            changed = True
        return changed

    def finish_main(self, ok: bool, *, waiting: bool = False, step: str | None = None,
                    now: float | None = None, stopped: bool = False) -> None:
        """stopped: the user pressed Stop. Shown as "stopped" (neutral), never as a failure."""
        now = time.time() if now is None else now
        m = self.main()
        if not m:
            return
        end = "stopped" if stopped else ("done" if ok else "failed")
        for a in self.agents:
            if a["parent"] == m["id"] and a["status"] == "running":
                _finish(a, end, now)
        if m["status"] == "running":
            _finish(m, end, now)
        if not stopped:
            self.drop_final(m["id"])
        if stopped:
            m["step"] = "Stopped by you"
            return
        if waiting:
            m["status"], m["step"] = "waiting", step or "Waiting for your answer"
        elif step:
            m["step"] = _short(step, 140)

    # -- summary + persistence ------------------------------------------------------------
    def summary(self) -> dict:
        ags = self.agents
        ends = [a["ended"] for a in ags if a["ended"]]
        starts = [a["started"] for a in ags if a["started"]]
        running = sum(1 for a in ags if a["status"] == "running")
        real = [a for a in ags if a.get("kind") != "background"]
        return {"agents": len(real), "running": sum(1 for a in real if a["status"] == "running"),
                "failed": sum(1 for a in real if a["status"] == "failed"),
                "stopped": sum(1 for a in real if a["status"] == "stopped"),
                "waiting": any(a["status"] == "waiting" for a in ags),
                "tokens": sum(a["tokens"] for a in ags),
                "started": min(starts) if starts else None,
                "duration_s": round(max(ends) - min(starts), 1) if ends and starts and not running else None}

    def save(self, *, force: bool = False, meta: dict | None = None) -> None:
        now = time.time()
        if not force and now - self._saved < 0.75:
            # throttled: still persist this change soon (a long tool call may follow with no new event)
            if self._timer is None:
                def later():
                    with self.lock:
                        self._timer = None
                        self.save(force=True)
                self._timer = threading.Timer(0.8, later)
                self._timer.daemon = True
                self._timer.start()
            return
        if self._timer is not None and force:
            self._timer.cancel()
            self._timer = None
        self._saved = now
        self.data["updated"] = now
        supersede_waiting(self.agents)
        self.data["summary"] = self.summary()
        if meta:
            self.data.update(meta)
        write_json(REQUESTS_DIR / self.request_id / self.file_name, self.data)
        if self.session_id and self.index:
            update_index(self.session_id, self.request_id, self.data)

    @classmethod
    def load(cls, request_id: str, session_id: str = "") -> "AgentRun":
        data = read_json(REQUESTS_DIR / request_id / "agents.json", None)
        run = cls(request_id, session_id or (data or {}).get("session_id", ""), data)
        return run


def update_index(session_id: str, request_id: str, data: dict) -> None:
    """Session index: one row per request, newest last. Locked (WebUI and gateway both write)."""
    AGENTS_INDEX_DIR.mkdir(parents=True, exist_ok=True)
    path = AGENTS_INDEX_DIR / f"{_safe(session_id)}.json"
    with open(path.with_suffix(".lock"), "w", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        idx = read_json(path, None) or {"session_id": session_id, "requests": {}}
        req = read_json(REQUESTS_DIR / request_id / "request.json", {}) or {}
        idx["requests"][request_id] = {
            "id": request_id, "created": req.get("created"), "route": req.get("route"),
            "status": req.get("status"), "prompt": _short(req.get("original_prompt"), 120),
            **(data.get("summary") or {}), "updated": data.get("updated")}
        idx["updated"] = time.time()
        write_json(path, idx)


def merged_agents(rid: str, base: Path | None = None) -> list[dict]:
    """A request's rows: agents.json plus every delegate's agents-<id>.json (hand-offs), oldest first."""
    d = (base or REQUESTS_DIR) / rid
    data = read_json(d / "agents.json", {}) or {}
    rows = list(data.get("agents", []))
    for f in sorted(d.glob("agents-*.json")):
        extra = read_json(f, {}) or {}
        rows += [a for a in extra.get("agents", []) if isinstance(a, dict)]
    return supersede_waiting(rows)


def merged_summary(rows: list[dict], base: dict | None = None) -> dict:
    """The index summary, recomputed when delegate rows exist (their tokens and agents count too)."""
    run = AgentRun("x", data={"agents": rows})
    return {**(base or {}), **run.summary()}


def session_view(session_id: str) -> dict:
    """Everything the agents panel shows for one session: requests (oldest first) with their agents."""
    idx = read_json(AGENTS_INDEX_DIR / f"{_safe(session_id)}.json", None) or {"requests": {}}
    out = []
    for rid in sorted(idx["requests"]):
        req = read_json(REQUESTS_DIR / rid / "request.json", {}) or {}
        row = dict(idx["requests"][rid])
        row["status"] = req.get("status", row.get("status"))
        row["agents_list"] = merged_agents(rid)
        if any(a.get("kind") in ("delegate", "background") or a.get("status") == "stopped" for a in row["agents_list"]):
            row.update(merged_summary(row["agents_list"]))
        row["waiting"] = any(a.get("status") == "waiting" for a in row["agents_list"])
        out.append(row)
    return {"session_id": session_id, "requests": out}


def load_events(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out
