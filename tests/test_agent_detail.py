"""Agents sheet click-through: everything one agent did (agents.agent_detail / agent_part / request_detail)
and the sidecar's read-only /agent/* and /request/detail routes."""
from __future__ import annotations
import importlib.util, json, sys
from importlib import import_module
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "plugin" / "command-centre"
if "command_centre" not in sys.modules:
    spec = importlib.util.spec_from_file_location("command_centre", PKG / "__init__.py",
                                                  submodule_search_locations=[str(PKG)])
    _m = importlib.util.module_from_spec(spec); sys.modules["command_centre"] = _m
    spec.loader.exec_module(_m)
ag = import_module("command_centre.agents")

RID = "20261111-120000-abc123"
SESS = "11111111-2222-3333-4444-555555555555"
SECRET = "sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789AbCdEfGh"
T0 = 1794400000.0


def _iso(t: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


def _line(typ, content, t, **extra):
    return json.dumps({"type": typ, "timestamp": _iso(t), "message": {"role": typ, "content": content}, **extra})


def _setup(tmp_path, monkeypatch, rows, req_extra=None):
    req_dir = tmp_path / "req"
    projects = tmp_path / "claude-projects"
    monkeypatch.setattr(ag, "REQUESTS_DIR", req_dir)
    monkeypatch.setattr(ag, "CLAUDE_PROJECTS", projects)
    monkeypatch.setattr(ag, "CODEX_SESSIONS", tmp_path / "codex" / "sessions")
    ag._detail_cache.clear(); ag._rollout_cache.clear()
    cwd = str(tmp_path / "proj")
    d = req_dir / RID
    d.mkdir(parents=True)
    req = {"id": RID, "status": "done", "route": "claude", "project": cwd, "original_prompt": "fix the login page",
           "executor": {"provider": "claude-cli", "model": "claude-opus-5-5", "session_id": SESS},
           "result": {"ok": True, "result": "Fixed it."}}
    req.update(req_extra or {})
    (d / "request.json").write_text(json.dumps(req), encoding="utf-8")
    (d / "agents.json").write_text(json.dumps({"request_id": RID, "agents": rows}), encoding="utf-8")
    folder = projects / ag._munge(cwd)
    folder.mkdir(parents=True)
    return d, folder


def _main_row(**kw):
    a = ag.new_agent("claude-1", "Claude Code", now=T0)
    a.update(status="done", ended=T0 + 100, duration_s=100, description="main worker", **kw)
    return a


def _transcript(folder):
    lines = [
        _line("user", [{"type": "text", "text": f"COMMAND CENTRE REQUEST {RID}\nFix the login page."}], T0 + 1),
        _line("assistant", [{"type": "thinking", "thinking": "", "signature": "x"},
                            {"type": "text", "text": "Looking at the login form."}], T0 + 2),
        _line("assistant", [{"type": "tool_use", "id": "toolu_bash1", "name": "Bash",
                             "input": {"command": "grep -n login src/app.js", "description": "Find the login code"}}], T0 + 3),
        _line("user", [{"type": "tool_result", "tool_use_id": "toolu_bash1",
                        "content": f"src/app.js:12: const key = '{SECRET}'\n" + "x" * 2000}], T0 + 4),
        _line("assistant", [{"type": "tool_use", "id": "toolu_env", "name": "Bash", "input": {"command": "printenv"}}], T0 + 5),
        _line("user", [{"type": "tool_result", "tool_use_id": "toolu_env", "content": "HOME=/x\nAPI_TOKEN=abc"}], T0 + 6),
        _line("assistant", [{"type": "tool_use", "id": "toolu_edit", "name": "Edit",
                             "input": {"file_path": "/p/src/app.js", "old_string": "a", "new_string": "b"}}], T0 + 7),
        _line("user", [{"type": "tool_result", "tool_use_id": "toolu_edit", "content": "boom", "is_error": True}], T0 + 8),
        _line("assistant", [{"type": "tool_use", "id": "toolu_sub1", "name": "Agent",
                             "input": {"description": "Check tests", "subagent_type": "general-purpose", "prompt": "Run the tests."}}], T0 + 9),
        _line("user", [{"type": "tool_result", "tool_use_id": "toolu_sub1", "content": "All green."}], T0 + 30),
        _line("assistant", [{"type": "tool_use", "id": "toolu_del", "name": "mcp__tabbie__delegate",
                             "input": {"task": "review", "mode": "read"}}], T0 + 31),
        _line("user", [{"type": "tool_result", "tool_use_id": "toolu_del", "content": "Looks fine."}], T0 + 50),
        _line("assistant", [{"type": "text", "text": "Fixed the login page."}], T0 + 60),
        # a later request in the same (resumed) session: outside this agent's window
        _line("user", [{"type": "text", "text": "something else"}], T0 + 500),
    ]
    (folder / f"{SESS}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    sub = folder / SESS / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent-abc.meta.json").write_text(json.dumps({"toolUseId": "toolu_sub1", "agentType": "general-purpose"}))
    (sub / "agent-abc.jsonl").write_text("\n".join([
        _line("user", "Run the tests.", T0 + 10, isSidechain=True),
        _line("assistant", [{"type": "tool_use", "id": "toolu_s1", "name": "Bash", "input": {"command": "npm test"}}], T0 + 11, isSidechain=True),
        _line("user", [{"type": "tool_result", "tool_use_id": "toolu_s1", "content": "12 passing"}], T0 + 20, isSidechain=True),
        _line("assistant", [{"type": "tool_use", "id": "toolu_hb", "name": "SubagentHandback", "input": {"message": "All green."}}], T0 + 21, isSidechain=True),
    ]) + "\n", encoding="utf-8")


def _rows():
    main = _main_row()
    sub = ag.new_agent("toolu_sub1", "Check tests", parent="claude-1", now=T0 + 9)
    sub.update(status="done", description="general-purpose")
    deleg = ag.new_agent("delegate-120001-ab12", "Codex · review", parent="claude-1", now=T0 + 32)
    deleg.update(status="done", kind="delegate", provider="astra", description="hand-off from Claude Code (read)")
    return [main, sub, deleg]


def test_main_worker_steps_from_transcript(tmp_path, monkeypatch):
    d, folder = _setup(tmp_path, monkeypatch, _rows())
    _transcript(folder)
    out = ag.agent_detail(RID, "claude-1")
    assert out["source"] == "claude transcript"
    assert out["prompt"]["text"].startswith("COMMAND CENTRE REQUEST")
    assert out["output"]["text"] == "Fixed the login page."            # the last words after the last tool call
    kinds = [s["kind"] for s in out["steps"]]
    assert kinds == ["text", "tool", "tool", "tool", "tool", "tool"]   # nothing from outside the window
    bash, env, edit, sub, deleg = out["steps"][1:]
    assert bash["label"].startswith("Ran: Find the login code") and bash["ok"] is True
    assert SECRET not in json.dumps(out)                                # scrubbed
    assert bash["out"]["more"] and len(bash["out"]["text"]) <= ag.DETAIL_PREVIEW
    assert env["out"]["text"].startswith("[withheld") and "API_TOKEN" not in json.dumps(out)
    assert edit["ok"] is False and edit["files"] == ["/p/src/app.js"]
    assert out["files"] == ["/p/src/app.js"]
    assert sub["agent"] == "toolu_sub1" and deleg["agent"] == "delegate-120001-ab12"
    assert out["counts"]["thinking_hidden"] == 1 and out["counts"]["failed"] == 1
    assert {c["id"] for c in out["children"]} == {"toolu_sub1", "delegate-120001-ab12"}
    # "Show more": one field in full, still scrubbed; a withheld output stays withheld
    full = ag.agent_part(RID, "claude-1", "out", bash["i"])
    assert full["len"] > 2000 and SECRET not in full["text"] and "x" * 1500 in full["text"]
    assert ag.agent_part(RID, "claude-1", "out", env["i"])["text"].startswith("[withheld")
    assert ag.agent_part(RID, "claude-1", "prompt")["text"].endswith("Fix the login page.")
    # paging
    page = ag.agent_detail(RID, "claude-1", offset=4, limit=2)
    assert page["total"] == 6 and [s["i"] for s in page["steps"]] == [4, 5]


def test_subagent_steps_and_handback(tmp_path, monkeypatch):
    d, folder = _setup(tmp_path, monkeypatch, _rows())
    _transcript(folder)
    out = ag.agent_detail(RID, "toolu_sub1")
    assert out["source"] == "claude sub-agent transcript"
    assert out["prompt"]["text"] == "Run the tests."
    assert out["output"]["text"] == "All green."
    assert [s["label"] for s in out["steps"]] == ["Ran: npm test"]
    assert out["parent"]["id"] == "claude-1"


def test_handoff_step_log_written_live_and_read(tmp_path, monkeypatch):
    d, _ = _setup(tmp_path, monkeypatch, _rows()[:1])
    run = ag.AgentRun(RID, "s1")
    run.file_name, run.index = "agents-120001-ab12.json", False
    row = run.start_main("Codex · review", "astra", description="hand-off from Claude Code (read)", now=T0 + 32)
    row["id"], row["parent"], row["kind"] = "delegate-120001-ab12", "claude-1", "delegate"
    run._main = row["id"]
    for ev in ({"type": "item.completed", "item": {"id": "i1", "type": "reasoning", "text": "Check the diff first."}},
               {"type": "item.completed", "item": {"id": "i2", "type": "command_execution", "command": "/bin/zsh -lc 'git diff'",
                                                   "aggregated_output": f"+token={SECRET}", "exit_code": 0, "status": "completed"}},
               {"type": "item.completed", "item": {"id": "i3", "type": "agent_message", "text": "No issues."}}):
        run.apply_codex(ev, now=T0 + 33)
    run.save(force=True)
    log = d / "steps" / "delegate-120001-ab12.jsonl"
    assert log.exists() and SECRET not in log.read_text()
    ddir = d / "delegates" / "120001-ab12"; ddir.mkdir(parents=True)
    (ddir / "prompt.md").write_text("You are a DELEGATE. Review the diff.")
    (ddir / "result.md").write_text("No issues.")
    out = ag.agent_detail(RID, "delegate-120001-ab12")
    assert out["source"] == "step-log" and out["agent"]["kind"] == "delegate"
    assert out["prompt"]["text"].startswith("You are a DELEGATE") and out["output"]["text"] == "No issues."
    assert [s["kind"] for s in out["steps"]] == ["thinking", "tool", "text"]
    assert out["steps"][1]["label"] == "Ran: git diff"
    # a normal main worker writes no step log (its transcript is the record)
    run2 = ag.AgentRun(RID, "s1")
    run2.start_main("Claude Code", "claude", description="main worker")
    run2.apply_claude({"type": "assistant", "message": {"id": "m1", "content": [{"type": "text", "text": "hi"}]}})
    assert not (d / "steps" / "claude-1.jsonl").exists()


def test_planner_without_log_uses_planning_transcript(tmp_path, monkeypatch):
    planner = ag.new_agent("astra-1", "GPT-6 Astra · planning", provider="astra", now=T0)
    planner.update(status="done", description="planner")
    tr = [{"role": "planner", "content": json.dumps({"kind": "questions", "summary": "Two things first",
                                                     "questions": [{"question": "Dark or light?"}]})},
          {"role": "user", "content": "Dark"},
          {"role": "planner", "content": json.dumps({"kind": "proposal", "summary": "Do X", "proposal_markdown": "- X"})}]
    _setup(tmp_path, monkeypatch, [planner], {"route": "astra", "planning_transcript": tr, "brief": "Goal: X"})
    out = ag.agent_detail(RID, "astra-1")
    assert out["source"] == "planning transcript" and out["note"]
    assert [s["kind"] for s in out["steps"]] == ["event", "user", "text"]
    assert out["steps"][0]["label"] == "Asked you 1 question" and out["output"]["text"] == "Goal: X"
    assert out["prompt"]["text"] == "fix the login page"


def test_codex_rollout(tmp_path, monkeypatch):
    tid = "01a1a1d4-e2ec-7470-86d4-03be664fe224"
    row = ag.new_agent("astra-1", "Codex", provider="astra", now=T0)
    row.update(status="done", ended=T0 + 50, description="main worker", thread_id=tid)
    _setup(tmp_path, monkeypatch, [row])
    roll = tmp_path / "codex" / "sessions" / "2026" / "11" / "11"
    roll.mkdir(parents=True)
    ev = lambda t, item: json.dumps({"timestamp": _iso(t), "type": "event_msg", "payload": {"type": "item_completed", "item": item}})
    (roll / f"rollout-2026-11-11T10-00-00-{tid}.jsonl").write_text("\n".join([
        ev(T0 + 1, {"type": "UserMessage", "content": [{"type": "text", "text": "<environment_context>x</environment_context>"}]}),
        ev(T0 + 1, {"type": "UserMessage", "content": [{"type": "text", "text": "Render the film."}]}),
        ev(T0 + 2, {"type": "Reasoning", "summary_text": [], "raw_content": []}),
        ev(T0 + 3, {"type": "CommandExecution", "id": "c1", "command": ["/bin/zsh", "-lc", "npm run render"],
                    "aggregated_output": "failed", "exit_code": 1, "status": "failed"}),
        ev(T0 + 4, {"type": "FileChange", "id": "f1", "changes": {"/m/a.js": {"type": "update", "unified_diff": "@@ -1 +1 @@"}},
                    "status": "completed"}),
        ev(T0 + 5, {"type": "AgentMessage", "content": [{"type": "Text", "text": "Render blocked by the sandbox."}]}),
    ]) + "\n")
    out = ag.agent_detail(RID, "astra-1")
    assert out["source"] == "codex rollout" and out["prompt"]["text"] == "Render the film."
    assert [s["label"] for s in out["steps"]] == ["Ran: npm run render", "Edited a.js"]
    assert out["steps"][0]["ok"] is False and out["files"] == ["/m/a.js"]
    assert out["output"]["text"] == "Render blocked by the sandbox." and out["counts"]["thinking_hidden"] == 1


def test_request_detail_and_sidecar_routes(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("cc_sidecar_detail", PKG / "sidecar.py")
    sc = importlib.util.module_from_spec(spec); spec.loader.exec_module(sc)
    d, folder = _setup(tmp_path, monkeypatch, _rows(), {"brief": "Goal: fix login"})
    for name in ("REQUESTS_DIR", "CLAUDE_PROJECTS", "CODEX_SESSIONS"):
        monkeypatch.setattr(sc.agents, name, getattr(ag, name))
    _transcript(folder)
    rd = ag.request_detail(RID)
    assert rd["prompt"]["text"] == "fix the login page" and rd["result"]["text"] == "Fixed it."
    assert rd["executor"]["model"] == "claude-opus-5-5" and [a["id"] for a in rd["agents"]][:1] == ["claude-1"]
    assert ag.request_part(RID, "brief")["text"] == "Goal: fix login"
    assert sc.detail_route("/agent/detail", {"rid": "nope", "agent": "claude-1"})[0] == 400
    assert sc.detail_route("/agent/detail", {"rid": RID, "agent": "../../etc"})[0] == 400
    assert sc.detail_route("/agent/detail", {"rid": RID, "agent": "missing"})[0] == 404
    assert sc.detail_route("/agent/part", {"rid": RID, "agent": "claude-1", "part": "env"})[0] == 400
    code, body = sc.detail_route("/agent/detail", {"rid": RID, "agent": "claude-1", "limit": "2"})
    assert code == 200 and len(body["steps"]) == 2 and body["total"] == 6
    code, body = sc.detail_route("/request/detail", {"rid": RID})
    assert code == 200 and body["rid"] == RID
