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
import shutil
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
DOC_EXT = {".pdf": "pdf"}     # file types whose content is checked (magic bytes) before attaching
DOC_MAX_BYTES = 500 * 1024 * 1024  # any other file (not a picture): a card in the reply, copied into the chat's
                                   # attachments; readable types open in the in-site viewer, the rest download
# Never attached, whatever the folder: secrets and keys.
_SECRET_NAME = re.compile(r"(^\.env(\..*)?$|^id_(rsa|dsa|ecdsa|ed25519)$|\.(pem|key|p12|pfx|keystore|jks)$|^\.netrc$|^\.npmrc$|^\.pypirc$|credentials)", re.I)
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


def _sha1_file(p: Path) -> str:
    h = hashlib.sha1()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def attach_docs(text: str, roots: list[str], dest: Path | None) -> str:
    """A reply's ATTACH lines naming any non-picture file -> `MEDIA: <copy>` (the chat shows a file card it can open and
    download); the copy goes into the chat's own attachments folder (dest), which the WebUI serves.
    Other ATTACH lines are left for strip_attach (images show in the Agents block)."""
    if not text or "ATTACH" not in text:
        return text

    def sub(m):
        raw = m.group(1).strip().strip("`'\"<>").strip()
        if not is_doc(raw):
            return m.group(0)
        p, why = check_doc(raw, roots)
        if p is None or dest is None:
            return f"(Couldn't attach {_short(Path(raw).name or raw, 120)}: {why if p is None else 'no chat to attach it to'}.)"
        try:
            dest.mkdir(parents=True, exist_ok=True)
            digest = _sha1_file(p)
            name = re.sub(r"[^\w.-]+", "-", p.name).strip("-.") or "file"  # a MEDIA path can't hold spaces
            out = dest / name
            if out.exists() and _sha1_file(out) != digest:  # another file of that name: keep both
                out = dest / f"{Path(name).stem}-{digest[:6]}{p.suffix.lower()}"
            if not out.exists():
                tmp = out.with_name(f".tmp-{out.name}-{os.getpid()}")
                shutil.copyfile(p, tmp)
                os.replace(tmp, out)
        except OSError:
            return f"(Couldn't attach {_short(p.name, 120)}: couldn't copy it.)"
        return f"MEDIA: {out}"
    return _ATTACH_RX.sub(sub, text)


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
            "webp": head[:4] == b"RIFF" and head[8:12] == b"WEBP", "pdf": head.startswith(b"%PDF-")}.get(ext, False)


def check_image(raw: str, roots: list[str]) -> tuple[Path | None, str]:
    """(resolved path, ext) when raw names an existing png/jpg/webp/gif inside one of roots, else
    (None, why not). Relative paths are taken from the first root. Symlinks are resolved first."""
    return _check(raw, roots, IMAGE_EXT, SHOT_MAX_BYTES, "not a png, jpg, webp or gif", "not a valid image")


def check_doc(raw: str, roots: list[str]) -> tuple[Path | None, str]:
    """check_image for files the reply shows as cards: any type, inside the run's folders, not a secret.
    A PDF must really be one (magic bytes); other types are passed through as they are."""
    name = Path(str(raw or "").strip().strip("`'\"<>")).name
    if _SECRET_NAME.search(name):
        return None, "looks like a secret or key file"
    if Path(name).suffix.lower() in DOC_EXT:
        return _check(raw, roots, DOC_EXT, DOC_MAX_BYTES, "not a pdf", "not a valid pdf")
    return _check(raw, roots, None, DOC_MAX_BYTES, "", "")


def is_doc(raw: str) -> bool:
    """Any attached file that isn't a picture (pictures show in the Agents block)."""
    p = Path(str(raw or "").strip().strip("`'\"<>"))
    return bool(p.name) and p.suffix.lower() not in IMAGE_EXT


def _check(raw: str, roots: list[str], exts: dict, max_bytes: int, bad_ext: str, bad_magic: str):
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
        ext = exts.get(p.suffix.lower()) if exts is not None else (p.suffix.lower().lstrip(".") or "file")
        if not ext:
            return None, bad_ext
        size = p.stat().st_size
        if size > max_bytes:
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
        if exts is not None:
            with open(p, "rb") as fh:
                if size < 16 or not _magic_ok(fh.read(16), ext):
                    return None, bad_magic
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
        if t in ("assistant", "user"):
            step_log_claude(self, ev, now)  # planner / hand-off runs keep no transcript: their own step log
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
        if is_doc(raw):  # a file, not a picture: the finished reply shows it as a card (attach_docs)
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
        step_log_codex(self, ev, now)  # planner / hand-off runs keep no transcript: their own step log
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


def _pid_alive(pid) -> bool:
    if not isinstance(pid, int) or pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True  # exists, not ours to signal
    return True


def _worker_gone(rid: str, rows: list[dict]) -> bool:
    """A request still marked executing whose recorded worker process no longer exists and has no agent
    running: it was killed (restart, reboot, a Stop the hub never reported). Without a recorded pid we
    can't tell, so it stays as recorded."""
    pid = (read_json(REQUESTS_DIR / rid / "live.json", {}) or {}).get("pid")
    if not isinstance(pid, int) or _pid_alive(pid):
        return False
    return not any(a.get("status") in ("running", "waiting") for a in rows)


def session_view(session_id: str) -> dict:
    """Everything the agents panel shows for one session: requests (oldest first) with their agents."""
    idx = read_json(AGENTS_INDEX_DIR / f"{_safe(session_id)}.json", None) or {"requests": {}}
    out = []
    for rid in sorted(idx["requests"]):
        req = read_json(REQUESTS_DIR / rid / "request.json", {}) or {}
        row = dict(idx["requests"][rid])
        row["status"] = req.get("status", row.get("status"))
        row["agents_list"] = merged_agents(rid)
        if row["status"] in ("executing", "planning") and _worker_gone(rid, row["agents_list"]):
            row["status"] = "stopped"  # display only: its worker exited without the request being closed
        if any(a.get("kind") in ("delegate", "background") or a.get("status") == "stopped" for a in row["agents_list"]):
            row.update(merged_summary(row["agents_list"]))
        row["waiting"] = any(a.get("status") == "waiting" for a in row["agents_list"])
        if row["status"] in ("awaiting_answer", "awaiting_answers") and isinstance(req.get("question"), dict):
            # the open question itself, so a view whose transcript never got the card can still ask it
            row["question"] = {**req["question"], "request_id": rid}
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


# =========================================================================================
# Agent detail: the Agents sheet's click-through view (GET /agent/detail, /agent/part in the sidecar).
# Everything one agent did, as one list of steps: the brief it got, its words, its reasoning when the
# provider records it, every tool call (short label + input + output), errors, the sub-agents and
# hand-offs it started (linked by id), files it changed, its final output. Read-only.
# Sources, best first:
#   Claude main worker  -> its Claude Code transcript (~/.claude/projects/<cwd>/<session>.jsonl), only the
#                          lines inside the agent's own time window (a chat resumes one session)
#   Claude sub-agent    -> <session>/subagents/agent-*.jsonl whose meta.json names the row's tool_use id
#   Codex worker / sub  -> its rollout ~/.codex/sessions/YYYY/MM/DD/rollout-*-<thread id>.jsonl
#   planner, hand-off   -> run ephemeral (no transcript): <request>/steps/<agent>.jsonl, written live by
#                          AgentRun (step_log), plus what the request keeps (planning transcript, brief,
#                          delegates/<id>/prompt.md + result.md)
#   anything else       -> the request's narration (short lines only)
# Every string is scrubbed (known secret values + secret patterns, hmem.trace.clean) and cut; outputs of
# commands that dump the environment or read credential files are withheld entirely.
# =========================================================================================

CLAUDE_PROJECTS = Path(os.environ.get("HMEM_CLAUDE_PROJECTS", Path.home() / ".claude" / "projects")).expanduser()
CODEX_SESSIONS = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser() / "sessions"
DETAIL_PAGE = 150          # steps per page
DETAIL_PREVIEW = 320       # chars of a tool step's input / output in the list
DETAIL_TEXT = 700          # chars of a text / thinking step in the list
DETAIL_FULL = 60_000       # chars of one field on "Show more" (well under the proxy's 512 KB cap)
STEP_LOG_MAX = 3000        # records per agent in <request>/steps/<agent>.jsonl
STEP_LOG_CHARS = 20_000    # chars per field in a step-log record
_DETAIL_MAX_LINE = 2_000_000  # transcript lines longer than this are images / blobs: skipped
_EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
_WITHHELD = "[withheld: this step may show credentials or environment variables]"
_ENV_DUMP_RX = re.compile(
    r"(?i)(^|[;&|(]\s*|\n\s*)(env|printenv|set|export -p|security\s+find-[a-z-]*password|gh auth token|op read|"
    r"aws configure export-credentials|gcloud auth print-|vercel env pull|supabase status|heroku config|"
    r"docker inspect|cat\s+[^|;&\n]*(\.env\b|credentials|auth\.json|id_rsa|\.pem|\.netrc|token))(\s|$|;|\|)")
_CRED_PATH_RX = re.compile(r"(?i)(/\.env(\.[\w.-]+)?$|/\.ssh/|/Keychains/|/\.aws/|/\.config/gh/|/\.codex/auth|"
                           r"/\.claude/\.credentials|\.(pem|key|p12|pfx)$|/\.netrc$|/\.npmrc$|/\.pypirc$|credentials)")
_detail_cache: dict = {}
_rollout_cache: dict = {}


def _hm_clean():
    try:
        from hmem import trace
    except ImportError:
        root = str(Path(__file__).resolve().parents[2])
        if root not in sys.path:
            sys.path.insert(0, root)
        try:
            from hmem import trace
        except ImportError:
            return None
    return trace.clean


def redact(text, n: int | None = None) -> tuple[str, int]:
    """(scrubbed text cut to n chars, length before cutting). Secrets are scrubbed before the cut, over a
    window wider than what is kept, so a secret at the edge can't leak half of itself."""
    s = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False, default=str, indent=1)
    full = len(s)
    if n:
        s = s[:max(n * 2, n + 4000)]
    c = _hm_clean()
    s = c(s) if c else _scrub(s)
    if n and len(s) > n:
        s = s[:n]
    return s, full


def _iso_ts(s) -> float | None:
    if isinstance(s, (int, float)):
        return float(s)
    if not isinstance(s, str) or not s:
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _blocks_text(c) -> str:
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        out = []
        for x in c:
            if isinstance(x, str):
                out.append(x)
            elif isinstance(x, dict):
                if x.get("type") in ("image", "input_image", "local_image"):
                    out.append("[image]")
                elif isinstance(x.get("text"), str):
                    out.append(x["text"])
        return "\n".join(out)
    if c is None:
        return ""
    return json.dumps(c, ensure_ascii=False, default=str)


def _sensitive(tool: str, inp) -> bool:
    """A step whose output may hold secrets: a command that prints env / credentials, a read of a key file."""
    if isinstance(inp, dict):
        cmd = inp.get("command") or inp.get("cmd") or ""
        if isinstance(cmd, list):
            cmd = " ".join(map(str, cmd))
        if cmd and _ENV_DUMP_RX.search(str(cmd)):
            return True
        for k in ("file_path", "path", "notebook_path"):
            if isinstance(inp.get(k), str) and _CRED_PATH_RX.search(inp[k]):
                return True
        return False
    return bool(_ENV_DUMP_RX.search(str(inp or "")))


def _claude_tool_input(name: str, inp: dict) -> str:
    if not isinstance(inp, dict):
        return _blocks_text(inp)
    if name == "Bash" and inp.get("command"):
        return str(inp["command"])
    if name in ("Agent", "Task") and inp.get("prompt"):
        head = " · ".join(str(inp[k]) for k in ("subagent_type", "description") if inp.get(k))
        return (head + "\n\n" if head else "") + str(inp["prompt"])
    if name == "Write" and inp.get("file_path"):
        return f"{inp['file_path']}\n\n{inp.get('content') or ''}"
    if name == "Edit" and inp.get("file_path"):
        return f"{inp['file_path']}\n\n- {inp.get('old_string') or ''}\n+ {inp.get('new_string') or ''}"
    if name == "Read" and inp.get("file_path"):
        return str(inp["file_path"]) + (f"  (lines {inp.get('offset')}+)" if inp.get("offset") else "")
    return json.dumps(inp, ensure_ascii=False, default=str, indent=1)


def _files_of(name: str, inp) -> list[str]:
    if name in _EDIT_TOOLS and isinstance(inp, dict):
        fp = inp.get("file_path") or inp.get("notebook_path")
        return [fp] if isinstance(fp, str) else []
    return []


_SYS_PREFIX = ("<system-reminder", "<command-", "<local-command", "Caveat:")


def claude_records(ev: dict, t: float | None = None) -> list[dict]:
    """Step records from one Claude event (a transcript line or a stream-json event: same shape).
    Records: {t, kind: user|text|thinking|tool|result, ...}; a result fills the tool step with its id."""
    t = _iso_ts(ev.get("timestamp")) if t is None else t
    typ, m = ev.get("type"), ev.get("message") or {}
    out: list[dict] = []
    if typ == "assistant":
        for b in m.get("content") or []:
            if not isinstance(b, dict):
                continue
            bt = b.get("type")
            if bt == "text" and str(b.get("text") or "").strip():
                out.append({"t": t, "kind": "text", "text": b["text"]})
            elif bt in ("thinking", "redacted_thinking"):
                txt = str(b.get("thinking") or "").strip()
                out.append({"t": t, "kind": "thinking", "text": txt} if txt else {"t": t, "kind": "thinking_hidden"})
            elif bt == "tool_use":
                name, inp = str(b.get("name") or "tool"), b.get("input") or {}
                if name == "SubagentHandback" and isinstance(inp, dict) and inp.get("message"):
                    out.append({"t": t, "kind": "text", "text": str(inp["message"])})  # a sub-agent's final report
                    continue
                rec = {"t": t, "kind": "tool", "id": str(b.get("id") or ""), "tool": name,
                       "label": _step_of(b) or name, "input": _claude_tool_input(name, inp),
                       "files": _files_of(name, inp)}
                if _sensitive(name, inp):
                    rec["withhold"] = True
                if name in _SUB_TOOLS:
                    rec["agent"] = rec["id"]
                out.append(rec)
    elif typ == "user":
        if ev.get("isMeta"):
            return out
        c = m.get("content")
        blocks = c if isinstance(c, list) else [{"type": "text", "text": c}] if isinstance(c, str) else []
        texts = []
        for b in blocks:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_result":
                out.append({"t": t, "kind": "result", "id": str(b.get("tool_use_id") or ""),
                            "output": _blocks_text(b.get("content")), "ok": not b.get("is_error")})
            elif b.get("type") == "text" and str(b.get("text") or "").strip():
                texts.append(b["text"])
        txt = "\n".join(texts).strip()
        if txt and not txt.startswith(_SYS_PREFIX):
            if txt.startswith("<task-notification"):
                out.append({"t": t, "kind": "event", "text": txt, "label": "Background task reported back"})
            elif txt.startswith("[Request interrupted"):
                out.append({"t": t, "kind": "event", "text": txt, "label": "Interrupted"})
            else:
                out.append({"t": t, "kind": "user", "text": txt})
    return out


def _cmd_str(cmd) -> str:
    if isinstance(cmd, list):
        cmd = cmd[-1] if len(cmd) >= 3 and str(cmd[1]).startswith("-") else " ".join(map(str, cmd))
    return _codex_cmd(cmd)


def codex_item_records(it: dict, t: float | None) -> list[dict]:
    """Step records from one Codex item: `codex exec --json` items (snake_case types) and rollout
    item_completed items (CamelCase types) map to the same records."""
    k = str(it.get("type") or "")
    kl = re.sub(r"(?<!^)(?=[A-Z])", "_", k).lower()
    iid = str(it.get("id") or "")
    if kl in ("agent_message", "assistant_message"):
        txt = it.get("text") if isinstance(it.get("text"), str) else _blocks_text(it.get("content"))
        return [{"t": t, "kind": "text", "text": txt}] if str(txt or "").strip() else []
    if kl == "user_message":
        txt = _blocks_text(it.get("content"))
        return [{"t": t, "kind": "user", "text": txt}] if txt.strip() else []
    if kl == "reasoning":
        txt = it.get("text") if isinstance(it.get("text"), str) else _blocks_text(it.get("summary_text") or it.get("summary"))
        return [{"t": t, "kind": "thinking", "text": txt}] if str(txt or "").strip() else [{"t": t, "kind": "thinking_hidden"}]
    if kl == "command_execution":
        cmd = _cmd_str(it.get("command"))
        bad = str(it.get("status") or "").lower() in ("failed", "error", "declined") or it.get("exit_code") not in (None, 0)
        rec = {"t": t, "kind": "tool", "id": iid, "tool": "Shell", "label": "Ran: " + _short(cmd, 140), "input": cmd,
               "output": str(it.get("aggregated_output") or it.get("output") or ""), "ok": not bad, "done": True}
        if it.get("exit_code") not in (None, 0):
            rec["output"] = f"(exit {it.get('exit_code')})\n" + rec["output"]
        if _sensitive("Shell", {"command": cmd}):
            rec["withhold"] = True
        return [rec]
    if kl == "file_change":
        ch = it.get("changes") or []
        if isinstance(ch, dict):
            paths = list(ch)
            body = "\n\n".join(f"{p}\n{(v or {}).get('unified_diff') or ''}" for p, v in ch.items() if isinstance(v, dict))
        else:
            paths = [str(c.get("path") or "") for c in ch if isinstance(c, dict) and c.get("path")]
            body = "\n".join(f"{c.get('kind') or 'update'} {c.get('path')}" for c in ch if isinstance(c, dict))
        names = [Path(p).name for p in paths]
        label = "Edited " + (names[0] if len(names) == 1 else f"{len(names)} files")
        return [{"t": t, "kind": "tool", "id": iid, "tool": "Edit", "label": label, "input": body,
                 "output": str(it.get("stdout") or ""), "files": paths, "done": True,
                 "ok": str(it.get("status") or "completed").lower() not in ("failed", "error", "declined")}]
    if kl == "mcp_tool_call":
        err = it.get("error")
        res = it.get("result")
        out = _blocks_text((res or {}).get("content") if isinstance(res, dict) else res)
        if err:
            out = (err.get("message") if isinstance(err, dict) else str(err)) + ("\n" + out if out else "")
        deleg = it.get("server") == "tabbie" and it.get("tool") == "delegate"
        return [{"t": t, "kind": "tool", "id": iid, "tool": f"{it.get('server') or 'mcp'}.{it.get('tool') or 'tool'}",
                 "label": "Handed off to Claude Code" if deleg else f"Used {it.get('tool') or 'a tool'}",
                 "input": json.dumps(it.get("arguments") or {}, ensure_ascii=False, default=str, indent=1),
                 "output": out, "done": True, "delegate": deleg or None,
                 "ok": not err and str(it.get("status") or "completed").lower() not in ("failed", "error")}]
    if kl in ("web_search", "extension") and (it.get("query") or it.get("kind") == "web.search"):
        q = str(it.get("query") or "")
        res = it.get("results") or []
        out = "\n".join(f"{r.get('title') or ''} {r.get('url') or ''}".strip() for r in res if isinstance(r, dict))
        return [{"t": t, "kind": "tool", "id": iid, "tool": "WebSearch", "label": "Searched the web for " + _short(q, 100),
                 "input": q, "output": out, "ok": True, "done": True}]
    if kl == "todo_list":
        items = it.get("items") or []
        txt = "\n".join(("[x] " if x.get("completed") else "[ ] ") + str(x.get("text") or "") for x in items if isinstance(x, dict))
        return [{"t": t, "kind": "event", "label": "Updated the plan", "text": txt}] if txt else []
    if kl == "collab_tool_call":
        tool = str(it.get("tool") or "")
        if tool.startswith("spawn"):
            return [{"t": t, "kind": "tool", "id": iid, "tool": "spawn_agent", "label": "Started a sub-agent",
                     "input": str(it.get("prompt") or ""), "output": "", "ok": True, "done": True,
                     "agent": (it.get("receiver_thread_ids") or [None])[0]}]
        return []
    if kl == "sub_agent_activity":
        return []
    if kl == "error":
        return [{"t": t, "kind": "error", "text": str(it.get("message") or "error")}]
    return []


_EXEC_CMD = re.compile(r"""\b(?:cmd|command)\s*:\s*("(?:[^"\\]|\\.)*")""")


def _codex_response_records(p: dict, t: float | None) -> list[dict]:
    """Older rollouts without item_completed events: the raw response items."""
    k = p.get("type")
    if k == "message":
        role = p.get("role")
        txt = _blocks_text(p.get("content"))
        if role == "assistant" and txt.strip():
            return [{"t": t, "kind": "text", "text": txt}]
        if role == "user" and txt.strip():
            return [{"t": t, "kind": "user", "text": txt}]
        return []
    if k == "reasoning":
        txt = _blocks_text(p.get("summary"))
        return [{"t": t, "kind": "thinking", "text": txt}] if txt.strip() else [{"t": t, "kind": "thinking_hidden"}]
    if k in ("function_call", "custom_tool_call", "local_shell_call"):
        raw = p.get("arguments") if k == "function_call" else (p.get("input") or p.get("action"))
        raw_s = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False, default=str)
        name = str(p.get("name") or k)
        cmds = []
        for m in _EXEC_CMD.finditer(raw_s or ""):
            try:
                cmds.append(json.loads(m.group(1)))
            except ValueError:
                pass
        if not cmds:
            try:
                a = json.loads(raw_s) if raw_s and raw_s.lstrip().startswith("{") else {}
                c = a.get("cmd") or a.get("command")
                if c:
                    cmds = [_cmd_str(c)]
            except ValueError:
                pass
        label = ("Ran: " + _short(cmds[0], 140) + (f" (+{len(cmds) - 1})" if len(cmds) > 1 else "")) if cmds \
            else ("Edited files" if name == "apply_patch" else f"Used {name}")
        rec = {"t": t, "kind": "tool", "id": str(p.get("call_id") or p.get("id") or ""), "tool": name, "label": label,
               "input": "\n".join(cmds) if cmds and name == "exec_command" else raw_s}
        if name == "apply_patch":
            rec["files"] = re.findall(r"^\*\*\* (?:Update|Add) File: (.+)$", raw_s or "", re.M)
        if _sensitive(name, raw_s):
            rec["withhold"] = True
        return [rec]
    if k in ("function_call_output", "custom_tool_call_output", "local_shell_call_output"):
        o = p.get("output")
        if isinstance(o, dict):
            o = o.get("content") or o.get("output") or json.dumps(o)
        return [{"t": t, "kind": "result", "id": str(p.get("call_id") or ""), "output": _blocks_text(o), "ok": True}]
    if k == "web_search_call":
        q = (p.get("action") or {}).get("query") or ""
        return [{"t": t, "kind": "tool", "id": str(p.get("id") or ""), "tool": "WebSearch", "done": True, "ok": True,
                 "label": "Searched the web" + (f" for {_short(q, 100)}" if q else ""), "input": q, "output": ""}]
    return []


def _codex_rollout(thread_id: str) -> Path | None:
    if not re.match(r"^[0-9A-Za-z-]{8,80}$", str(thread_id or "")):
        return None
    hit = _rollout_cache.get(thread_id)
    if hit and hit.exists():
        return hit
    found = sorted(CODEX_SESSIONS.glob(f"*/*/*/rollout-*-{thread_id}.jsonl"))
    if found:
        _rollout_cache[thread_id] = found[-1]
        return found[-1]
    return None


def _skip_codex_user(txt: str) -> bool:
    s = txt.lstrip()
    return s.startswith(("<environment_context", "<user_instructions", "# AGENTS.md", "<skills_instructions",
                         "<permissions", "<turn_aborted"))


def _read_records(path: Path, kind: str, t0: float | None, t1: float | None) -> list[dict]:
    """All step records of one transcript / rollout, cached on (size, mtime, window)."""
    try:
        st = path.stat()
    except OSError:
        return []
    key = (str(path), st.st_size, st.st_mtime_ns, kind, t0, t1)
    hit = _detail_cache.get(key)
    if hit is not None:
        return hit
    recs: list[dict] = []
    alt: list[dict] = []   # codex: raw response items, used when the rollout has no item_completed events
    try:
        fh = open(path, "rb")
    except OSError:
        return []
    with fh:
        for raw in fh:
            if len(raw) > _DETAIL_MAX_LINE:
                continue
            try:
                e = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                continue
            if not isinstance(e, dict):
                continue
            t = _iso_ts(e.get("timestamp"))
            if t is not None and ((t0 and t < t0 - 3) or (t1 and t > t1 + 3)):
                continue
            if kind == "claude":
                if e.get("type") in ("user", "assistant"):
                    recs += claude_records(e, t)
            else:
                p = e.get("payload") or {}
                if e.get("type") == "event_msg" and p.get("type") == "item_completed":
                    recs += codex_item_records(p.get("item") or {}, t)
                elif e.get("type") == "response_item":
                    alt += _codex_response_records(p, t)
                elif e.get("type") == "event_msg" and p.get("type") in ("error", "stream_error") and p.get("message"):
                    recs.append({"t": t, "kind": "error", "text": str(p["message"])})
    if kind == "codex":
        if not any(r["kind"] in ("tool", "text") for r in recs):
            recs = [r for r in recs if r["kind"] == "error"] + alt
        recs = [r for r in recs if not (r["kind"] == "user" and _skip_codex_user(r.get("text") or ""))]
    if len(_detail_cache) > 24:
        _detail_cache.clear()
    _detail_cache[key] = recs
    return recs


def assemble_steps(recs: list[dict]) -> list[dict]:
    """Records -> steps: a tool result fills its tool step; the first user message is the prompt."""
    steps: list[dict] = []
    by_id: dict[str, dict] = {}
    for r in recs:
        if r.get("kind") == "result":
            s = by_id.get(r.get("id") or "")
            if s is not None:
                s["output"], s["ok"], s["done"] = r.get("output") or "", r.get("ok", True), True
                if r.get("t"):
                    s["ended"] = r["t"]
            continue
        s = dict(r)
        if s.get("kind") == "user" and all(x["kind"] in ("thinking", "thinking_hidden") for x in steps):
            s["kind"] = "prompt"
        if s.get("kind") == "tool" and s.get("id"):
            prev = by_id.get(s["id"])
            if prev is not None:      # a step-log "started" record followed by its "completed" record
                prev.update({k: v for k, v in s.items() if v not in (None, "", [])})
                continue
            by_id[s["id"]] = s
        steps.append(s)
    return steps


# -- step log (agents with no saved transcript: planner, hand-offs) --------------------------
def _step_log_path(base: Path | None, rid: str, agent_id: str) -> Path:
    return (base or REQUESTS_DIR) / rid / "steps" / f"{_safe(agent_id)}.jsonl"


def _cut_rec(r: dict) -> dict:
    out = {}
    for k, v in r.items():
        if isinstance(v, str) and len(v) > STEP_LOG_CHARS:
            v = v[:STEP_LOG_CHARS] + f"\n…[cut: {len(v) - STEP_LOG_CHARS} more chars]"
        out[k] = v
    if out.get("withhold") and "output" in out:
        out["output"] = _WITHHELD
    for k in ("input", "output", "text"):
        if isinstance(out.get(k), str):
            out[k] = _scrub(out[k])
    return out


def write_step_log(run: "AgentRun", agent_id: str, recs: list[dict]) -> None:
    """Append records to <request>/steps/<agent>.jsonl (capped). Never raises: a log must not break a run."""
    if not recs:
        return
    try:
        n = run.__dict__.setdefault("_steplog_n", {})
        if n.get(agent_id, 0) >= STEP_LOG_MAX:
            return
        p = _step_log_path(run.base, run.request_id, agent_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            for r in recs:
                if r.get("kind") == "thinking_hidden":
                    continue
                f.write(json.dumps(_cut_rec(r), ensure_ascii=False, default=str) + "\n")
                n[agent_id] = n.get(agent_id, 0) + 1
    except Exception:  # noqa: BLE001
        pass


def _no_transcript(a: dict | None) -> bool:
    """Runs that keep no transcript of their own: the planner (codex --ephemeral) and hand-offs."""
    return bool(a) and (a.get("kind") == "delegate" or a.get("description") == "planner")


def step_log_claude(run: "AgentRun", ev: dict, now: float) -> None:
    main = run.main()
    if not _no_transcript(main) or ev.get("type") not in ("assistant", "user"):
        return
    aid = ev.get("parent_tool_use_id") or main["id"]
    write_step_log(run, aid, claude_records(ev, now))


def step_log_codex(run: "AgentRun", ev: dict, now: float) -> None:
    main = run.main()
    if not _no_transcript(main):
        return
    if ev.get("type") == "item.completed":
        write_step_log(run, main["id"], codex_item_records(ev.get("item") or {}, now))
    elif ev.get("type") in ("turn.failed", "error"):
        err = ev.get("error")
        msg = err.get("message") if isinstance(err, dict) else ev.get("message")
        write_step_log(run, main["id"], [{"t": now, "kind": "error", "text": str(msg or "failed")}])


def _read_step_log(p: Path) -> list[dict]:
    try:
        return [r for r in load_events(p) if isinstance(r, dict)]
    except OSError:
        return []


# -- finding an agent's transcript ---------------------------------------------------------
def _munge(cwd: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def _claude_main_transcript(rid: str, req: dict, a: dict, base: Path | None) -> Path | None:
    """The Claude Code transcript a main worker wrote: the request's recorded session, else (a run that
    started a fresh session, e.g. before a question was answered) the newest transcript in the project's
    folder that names this request and was written during the agent's run."""
    d = (base or REQUESTS_DIR) / rid
    ex = req.get("executor") or {}
    cwd = req.get("project") or str((d / "work").resolve())
    folder = CLAUDE_PROJECTS / _munge(cwd)
    sess = str(ex.get("session_id") or "")
    t0 = a.get("started") or 0
    t1 = a.get("ended")
    cands = []
    if re.match(r"^[0-9A-Za-z-]{8,80}$", sess) and (folder / f"{sess}.jsonl").exists():
        cands.append(folder / f"{sess}.jsonl")
    try:
        more = [p for p in folder.glob("*.jsonl") if p not in cands and p.stat().st_mtime >= t0 - 5]
    except OSError:
        more = []
    more.sort(key=lambda p: p.stat().st_mtime)
    marker = f"COMMAND CENTRE REQUEST {rid}".encode()
    for p in cands + more[:12]:
        if p in cands:
            if any(True for r in _read_records(p, "claude", t0, t1) if r["kind"] != "result"):
                return p
            continue
        try:
            with open(p, "rb") as fh:
                head = fh.read(400_000)
        except OSError:
            continue
        if marker in head:
            return p
    return cands[0] if cands else None


def _claude_sub_transcript(main_path: Path | None, tool_use_id: str) -> Path | None:
    if not main_path:
        return None
    d = main_path.with_suffix("") / "subagents"
    if not d.is_dir():
        return None
    for meta in d.glob("*.meta.json"):
        m = read_json(meta, {}) or {}
        if m.get("toolUseId") == tool_use_id:
            p = meta.with_name(meta.name[: -len(".meta.json")] + ".jsonl")
            return p if p.exists() else None
    return None


def _main_of(a: dict, rows: list[dict]) -> dict | None:
    seen = set()
    while a and a.get("parent") and a["id"] not in seen:
        seen.add(a["id"])
        if a.get("kind") == "delegate":
            return a
        a = next((x for x in rows if x["id"] == a["parent"]), None)
    return a


def _planner_records(req: dict, rid: str, base: Path | None) -> tuple[list[dict], str]:
    """What the request keeps of a planner run: its questions / proposals and the user's replies."""
    recs: list[dict] = []
    final = ""
    for t in req.get("planning_transcript") or []:
        if t.get("role") == "planner":
            try:
                data = json.loads(t.get("content") or "{}")
            except (ValueError, TypeError):
                data = {"summary": str(t.get("content") or "")}
            kind = data.get("kind")
            if kind == "questions":
                qs = data.get("questions") or []
                body = "\n\n".join(str(q.get("question") or q.get("header") or q) if isinstance(q, dict) else str(q) for q in qs)
                recs.append({"kind": "event", "label": f"Asked you {len(qs)} question{'s' if len(qs) != 1 else ''}",
                             "text": (str(data.get("summary") or "") + "\n\n" + body).strip()})
            else:
                body = str(data.get("proposal_markdown") or data.get("brief") or data.get("summary") or "")
                recs.append({"kind": "text", "text": (str(data.get("summary") or "") + "\n\n" + body).strip()})
                final = body or final
        elif t.get("role") == "user":
            recs.append({"kind": "user", "text": str(t.get("content") or "")})
    return recs, final


def _fallback_records(rid: str, aid: str, base: Path | None) -> list[dict]:
    data = read_json((base or REQUESTS_DIR) / rid / "agents.json", {}) or {}
    recs = []
    for n in data.get("narration") or []:
        if n.get("agent") != aid:
            continue
        if n.get("kind") == "op":
            recs.append({"t": n.get("at"), "kind": "tool", "id": n.get("id") or "", "tool": n.get("tool") or "tool",
                         "label": n.get("done") or n.get("text"), "input": "", "output": "",
                         "ok": n.get("status") != "failed", "done": n.get("status") != "running"})
        else:
            recs.append({"t": n.get("at"), "kind": "text", "text": n.get("text") or ""})
    return recs


def _read_text(p: Path, n: int = 400_000) -> str:
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read(n)
    except OSError:
        return ""


def _agent_source(rid: str, a: dict, rows: list[dict], req: dict, base: Path | None) -> dict:
    """{recs, source, prompt, output, note} for one agent row."""
    d = (base or REQUESTS_DIR) / rid
    aid = a["id"]
    out = {"recs": [], "source": "none", "prompt": None, "output": None, "note": None}
    log = _step_log_path(base, rid, aid)
    if a.get("kind") == "delegate":
        ddir = d / "delegates" / aid.removeprefix("delegate-")
        out["prompt"] = _read_text(ddir / "prompt.md") or None
        out["output"] = _read_text(ddir / "result.md") or _read_text(ddir / "last.txt") or None
        out["recs"] = _read_step_log(log)
        out["source"] = "step-log" if out["recs"] else "hand-off files"
        if not out["recs"]:
            out["note"] = "Hand-offs run without a saved transcript. Steps are recorded for hand-offs started from now on."
        return out
    if a.get("description") == "planner":
        recs, final = _planner_records(req, rid, base)
        log_recs = _read_step_log(log)
        out["recs"] = log_recs + recs
        out["prompt"] = str(req.get("effective_text") or req.get("original_prompt") or "") or None
        out["output"] = str(req.get("brief") or "") or final or None
        out["source"] = "step-log" if log_recs else "planning transcript"
        if not log_recs:
            out["note"] = "The planner runs without a saved transcript, so only its questions and plans are shown. Its tool calls are recorded for plans made from now on."
        return out
    if a.get("kind") == "background":
        parent = next((x for x in rows if x["id"] == a.get("parent")), None)
        src = _agent_source(rid, parent, rows, req, base) if parent else out
        step = [s for s in assemble_steps(src["recs"]) if s.get("id") == aid]
        out.update(recs=step, source=src["source"])
        return out
    is_codex = str(a.get("provider") or "").startswith(("astra", "codex"))
    if is_codex:
        tid = a.get("thread_id") or (aid if a.get("parent") else None)
        p = _codex_rollout(tid) if tid else None
        if p:
            t0 = a.get("started") if a.get("parent") is None else None
            t1 = a.get("ended") if a.get("parent") is None else None
            out["recs"], out["source"] = _read_records(p, "codex", t0, t1), "codex rollout"
            return out
    else:
        top = _main_of(a, rows)
        if top is not None and top.get("kind") == "delegate":
            out["recs"] = _read_step_log(log)
            out["source"] = "step-log" if out["recs"] else "none"
            if not out["recs"]:
                out["note"] = "This hand-off's sub-agent ran without a saved transcript."
            return out
        if top is not None and top.get("parent") is None:
            mp = _claude_main_transcript(rid, req, top, base)
            if a is top and mp:
                out["recs"], out["source"] = _read_records(mp, "claude", a.get("started"), a.get("ended")), "claude transcript"
                return out
            sp = _claude_sub_transcript(mp, aid)
            if sp:
                out["recs"], out["source"] = _read_records(sp, "claude", None, None), "claude sub-agent transcript"
                call = [s for s in assemble_steps(_read_records(mp, "claude", None, None)) if s.get("id") == aid]
                if call and call[0].get("done") and call[0].get("output") \
                        and not str(call[0]["output"]).startswith("Async agent launched"):
                    out["output"], out["soft_output"] = call[0]["output"], True  # what it handed back
                return out
            if mp:  # no transcript of its own: the call that started it (brief + what it returned)
                call = [s for s in assemble_steps(_read_records(mp, "claude", None, None)) if s.get("id") == aid]
                if call:
                    out["prompt"], out["output"] = call[0].get("input"), call[0].get("output")
                    out["source"], out["note"] = "parent transcript", "Only the brief and the result were saved for this sub-agent."
                    return out
    out["recs"] = _fallback_records(rid, aid, base)
    out["source"] = "narration" if out["recs"] else "none"
    out["note"] = "No transcript was found for this agent; showing the short progress lines it reported." if out["recs"] \
        else "No transcript was found for this agent."
    return out


def _preview(text, n: int) -> dict | None:
    if text is None or text == "":
        return None
    s, full = redact(text, n)
    return {"text": s, "len": full, "more": full > len(s)}


def _public_step(i: int, s: dict) -> dict:
    k = s.get("kind")
    out = {"i": i, "kind": k, "t": s.get("t")}
    if k == "tool":
        label = s.get("label") or s.get("tool") or ""
        out.update(tool=s.get("tool"), label=redact(past_tense(label) if s.get("done") else label, 200)[0],
                   ok=s.get("ok"), done=bool(s.get("done")), ended=s.get("ended"))
        out["in"] = _preview(s.get("input"), DETAIL_PREVIEW)
        out["out"] = {"text": _WITHHELD, "len": len(_WITHHELD), "more": False} if s.get("withhold") and s.get("output") \
            else _preview(s.get("output"), DETAIL_PREVIEW)
        for key in ("agent", "files", "delegate"):
            if s.get(key):
                out[key] = s[key]
    else:
        out["label"] = s.get("label")
        out["text"] = _preview(s.get("text"), DETAIL_TEXT)
    return out


def _link_children(steps: list[dict], a: dict, rows: list[dict]) -> None:
    """Point each tool step that started an agent at that agent's row (sub-agents by tool_use id;
    hand-offs by order: the n-th delegate call made the n-th hand-off row of this agent)."""
    ids = {r["id"] for r in rows}
    for s in steps:
        if s.get("agent") and s["agent"] not in ids:
            s.pop("agent", None)
        if s.get("kind") == "tool" and s.get("id") in ids and not s.get("agent"):
            s["agent"] = s["id"]   # e.g. a background command that has a row of its own
    hand = sorted((r for r in rows if r.get("kind") == "delegate" and r.get("parent") == a["id"]),
                  key=lambda r: r.get("started") or 0)
    calls = [s for s in steps if s.get("kind") == "tool"
             and (s.get("delegate") or re.search(r"(^|__|\.)delegate$", str(s.get("tool") or "")))]
    for s, r in zip(calls, hand):
        s["agent"] = r["id"]


def _row_public(a: dict) -> dict:
    keep = ("id", "parent", "name", "description", "provider", "model", "status", "step", "started", "ended",
            "duration_s", "tokens", "output_tokens", "tool_uses", "kind", "to", "turns", "async")
    return {k: (redact(a[k], 300)[0] if k in ("step", "name") and isinstance(a.get(k), str) else a.get(k))
            for k in keep if k in a}


def agent_steps(rid: str, agent_id: str, base: Path | None = None):
    """(row, steps, source info, rows) for one agent, or None when the request / agent doesn't exist."""
    rows = merged_agents(rid, base)
    a = next((x for x in rows if x.get("id") == agent_id), None)
    if a is None:
        return None
    req = read_json((base or REQUESTS_DIR) / rid / "request.json", {}) or {}
    src = _agent_source(rid, a, rows, req, base)
    steps = [s for s in assemble_steps(src["recs"]) if s.get("kind") != "thinking_hidden"]
    hidden = sum(1 for r in src["recs"] if r.get("kind") == "thinking_hidden")
    if src["prompt"] is None and steps and steps[0].get("kind") == "prompt":
        src["prompt"] = steps.pop(0).get("text")
    else:
        steps = [s for s in steps if s.get("kind") != "prompt"] if src["prompt"] is not None else steps
    if (src["output"] is None or src.get("soft_output")) and a.get("status") != "running":
        last = next((s for s in reversed(steps) if s.get("kind") in ("text", "tool")), None)
        if last is not None and last.get("kind") == "text":
            src["output"] = steps.pop(len(steps) - 1 - steps[::-1].index(last)).get("text")
    _link_children(steps, a, rows)
    src["hidden_thinking"] = hidden
    return a, steps, src, rows


def agent_detail(rid: str, agent_id: str, offset: int = 0, limit: int = DETAIL_PAGE, base: Path | None = None) -> dict | None:
    got = agent_steps(rid, agent_id, base)
    if got is None:
        return None
    a, steps, src, rows = got
    offset = max(0, int(offset or 0))
    limit = max(1, min(int(limit or DETAIL_PAGE), 400))
    files: list[str] = []
    for s in steps:
        for f in s.get("files") or []:
            if f not in files:
                files.append(f)
    counts = {"tools": sum(1 for s in steps if s.get("kind") == "tool"),
              "failed": sum(1 for s in steps if s.get("kind") == "tool" and s.get("ok") is False),
              "texts": sum(1 for s in steps if s.get("kind") == "text"),
              "thinking": sum(1 for s in steps if s.get("kind") == "thinking"),
              "thinking_hidden": src["hidden_thinking"]}
    parent = next((x for x in rows if x["id"] == a.get("parent")), None)
    children = [{"id": r["id"], "name": redact(r.get("name") or "", 160)[0], "kind": r.get("kind"),
                 "status": r.get("status"), "description": r.get("description")}
                for r in rows if r.get("parent") == a["id"]]
    return {"rid": rid, "agent": _row_public(a), "source": src["source"], "note": src["note"],
            "parent": {"id": parent["id"], "name": parent.get("name")} if parent else None,
            "prompt": _preview(src["prompt"], 1200), "output": _preview(src["output"], 2400),
            "files": [redact(f, 400)[0] for f in files[:100]], "files_total": len(files),
            "children": children, "counts": counts, "total": len(steps), "offset": offset,
            "steps": [_public_step(i, s) for i, s in enumerate(steps[offset:offset + limit], start=offset)],
            "running": a.get("status") == "running"}


def agent_part(rid: str, agent_id: str, part: str, i: int = -1, base: Path | None = None) -> dict | None:
    """One field in full (up to DETAIL_FULL chars, scrubbed): part prompt | output | in | out | text."""
    got = agent_steps(rid, agent_id, base)
    if got is None:
        return None
    a, steps, src, _rows = got
    if part in ("prompt", "output"):
        raw = src.get(part)
    else:
        if not 0 <= i < len(steps):
            return None
        s = steps[i]
        if part == "out" and s.get("withhold"):
            raw = _WITHHELD
        else:
            raw = s.get({"in": "input", "out": "output", "text": "text"}.get(part, ""))
    if raw is None:
        return None
    txt, full = redact(raw, DETAIL_FULL)
    return {"rid": rid, "agent": agent_id, "part": part, "i": i, "text": txt, "len": full, "truncated": full > len(txt)}


def request_detail(rid: str, base: Path | None = None) -> dict | None:
    """The request itself (the sheet's request header): what was asked, how it was routed, who ran it,
    the brief, the result, and its agents (ids for drilling in)."""
    d = (base or REQUESTS_DIR) / rid
    req = read_json(d / "request.json", None)
    if not isinstance(req, dict):
        return None
    ex = req.get("executor") or {}
    res = req.get("result") or {}
    result = res.get("result") if isinstance(res, dict) else res
    error = res.get("error") if isinstance(res, dict) and not res.get("ok") else None
    rows = merged_agents(rid, base)
    return {"rid": rid, "status": req.get("status"), "created": req.get("created"), "updated": req.get("updated"),
            "route": req.get("route"), "route_reason": req.get("route_reason"),
            "project": req.get("project"), "project_source": req.get("project_source"),
            "planner": req.get("planner"),
            "executor": {k: ex.get(k) for k in ("provider", "model", "effort", "profile", "resumed", "billing")} if ex else None,
            "usage": ex.get("usage") if isinstance(ex.get("usage"), dict) else None,
            "prompt": _preview(req.get("effective_text") or req.get("original_prompt"), 1500),
            "brief": _preview(req.get("brief"), 1500),
            "result": _preview(result if isinstance(result, str) else None, 2400),
            "error": _preview(error if isinstance(error, str) else None, 600),
            "modifications": [redact(m, 400)[0] for m in (req.get("modifications") or [])[-5:]],
            "agents": [_row_public(a) for a in rows]}


def request_part(rid: str, part: str, base: Path | None = None) -> dict | None:
    req = read_json((base or REQUESTS_DIR) / rid / "request.json", None)
    if not isinstance(req, dict):
        return None
    res = req.get("result") or {}
    raw = {"prompt": req.get("effective_text") or req.get("original_prompt"), "brief": req.get("brief"),
           "result": res.get("result") if isinstance(res, dict) else res,
           "error": res.get("error") if isinstance(res, dict) and not res.get("ok") else None}.get(part)
    if not isinstance(raw, str):
        return None
    txt, full = redact(raw, DETAIL_FULL)
    return {"rid": rid, "part": part, "text": txt, "len": full, "truncated": full > len(txt)}
