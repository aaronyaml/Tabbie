"""Per-request agent records: the main worker plus every subagent it spawns.

Fed from Claude Code stream-json events (main worker, Agent/Task subagents via parent_tool_use_id and
system task_* events) and Codex `--json` events (Astra). Persisted per request as
state/requests/<id>/agents.json, with a per-session index at state/agents/<session>.json that the
WebUI sidecar serves.

Record: {id, parent, name, description, provider, model, status (running|done|failed|waiting),
step, started, ended, duration_s, tokens, output_tokens, tool_uses}
`tokens` follows Claude Code's own subagent figure (totalTokens): the agent's context at its latest
call (input + cache write + cache read + output). `output_tokens` is cumulative.
"""

from __future__ import annotations

import fcntl
import json
import time
from pathlib import Path

from .common import REQUESTS_DIR, STATE_DIR, read_json, write_json

AGENTS_INDEX_DIR = STATE_DIR / "agents"
_SUB_TOOLS = ("Agent", "Task")


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
            "tokens": 0, "output_tokens": 0, "tool_uses": 0}


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


class AgentRun:
    """Agent records for one request. Mutated by apply_* (pure, testable), persisted by save()."""

    def __init__(self, request_id: str, session_id: str = "", data: dict | None = None):
        self.request_id, self.session_id = request_id, session_id
        self.data = data or {"request_id": request_id, "session_id": session_id, "agents": []}
        self._msgs: dict[str, dict] = {}      # message id -> {agent, out}
        self._main: str | None = None
        self._saved = 0.0

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
            main["model"] = ev.get("model") or main["model"]
            main["step"] = "Started"
            return True
        if t == "system" and st in ("task_started", "task_progress", "task_notification"):
            tid = ev.get("tool_use_id")
            if not tid:
                return False
            a = self._sub(tid, ev.get("description") or ev.get("subagent_type") or "Subagent", now)
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
                if b.get("type") == "tool_use":
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
                a = self.get(b.get("tool_use_id") or "")
                if not a or a["parent"] is None:
                    continue
                tur = ev.get("tool_use_result") if isinstance(ev.get("tool_use_result"), dict) else {}
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
            for a in self.agents:
                if a["status"] == "running" and (a["parent"] == main["id"] or a["parent"] == "sub"):
                    _finish(a, "done" if ok else "failed", now)
            _finish(main, "done" if ok else "failed", now,
                    (ev["duration_ms"] / 1000) if ev.get("duration_ms") else None)
            main["step"] = "Done" if ok else _short(ev.get("result") or "Failed", 140)
            if ev.get("num_turns") is not None:
                main["turns"] = ev["num_turns"]
            return True
        return False

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
            if k == "agent_message" and it.get("text"):
                step = _short(it["text"], 140)
            elif k == "command_execution":
                step = "Running: " + _short(it.get("command"), 120)
                if t == "item.started":
                    main["tool_uses"] += 1
            elif k == "mcp_tool_call":
                step = f"Using {it.get('tool') or 'a tool'}"
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

    def finish_main(self, ok: bool, *, waiting: bool = False, step: str | None = None,
                    now: float | None = None) -> None:
        now = time.time() if now is None else now
        m = self.main()
        if not m:
            return
        for a in self.agents:
            if a["parent"] == m["id"] and a["status"] == "running":
                _finish(a, "done" if ok else "failed", now)
        if m["status"] == "running":
            _finish(m, "done" if ok else "failed", now)
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
        return {"agents": len(ags), "running": running,
                "failed": sum(1 for a in ags if a["status"] == "failed"),
                "waiting": any(a["status"] == "waiting" for a in ags),
                "tokens": sum(a["tokens"] for a in ags),
                "started": min(starts) if starts else None,
                "duration_s": round(max(ends) - min(starts), 1) if ends and starts and not running else None}

    def save(self, *, force: bool = False, meta: dict | None = None) -> None:
        now = time.time()
        if not force and now - self._saved < 0.75:
            return
        self._saved = now
        self.data["updated"] = now
        supersede_waiting(self.agents)
        self.data["summary"] = self.summary()
        if meta:
            self.data.update(meta)
        write_json(REQUESTS_DIR / self.request_id / "agents.json", self.data)
        if self.session_id:
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
    with open(path.with_suffix(".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        idx = read_json(path, None) or {"session_id": session_id, "requests": {}}
        req = read_json(REQUESTS_DIR / request_id / "request.json", {}) or {}
        idx["requests"][request_id] = {
            "id": request_id, "created": req.get("created"), "route": req.get("route"),
            "status": req.get("status"), "prompt": _short(req.get("original_prompt"), 120),
            **(data.get("summary") or {}), "updated": data.get("updated")}
        idx["updated"] = time.time()
        write_json(path, idx)


def session_view(session_id: str) -> dict:
    """Everything the agents panel shows for one session: requests (oldest first) with their agents."""
    idx = read_json(AGENTS_INDEX_DIR / f"{_safe(session_id)}.json", None) or {"requests": {}}
    out = []
    for rid in sorted(idx["requests"]):
        data = read_json(REQUESTS_DIR / rid / "agents.json", {}) or {}
        req = read_json(REQUESTS_DIR / rid / "request.json", {}) or {}
        row = dict(idx["requests"][rid])
        row["status"] = req.get("status", row.get("status"))
        row["agents_list"] = supersede_waiting(data.get("agents", []))
        row["waiting"] = any(a.get("status") == "waiting" for a in row["agents_list"])
        out.append(row)
    return {"session_id": session_id, "requests": out}


def load_events(path: Path) -> list[dict]:
    out = []
    for line in path.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out
