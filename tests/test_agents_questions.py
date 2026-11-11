"""Agent records from worker event streams, and hermes-question block extraction."""
from __future__ import annotations
import importlib.util, json, sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "plugin" / "command-centre"
if "command_centre" not in sys.modules:
    spec = importlib.util.spec_from_file_location("command_centre", PKG / "__init__.py",
                                                  submodule_search_locations=[str(PKG)])
    cc = importlib.util.module_from_spec(spec); sys.modules["command_centre"] = cc
    spec.loader.exec_module(cc)
from importlib import import_module
agents = import_module("command_centre.agents")
questions = import_module("command_centre.questions")
providers = import_module("command_centre.providers")

FIX = Path(__file__).parent / "fixtures" / "claude_two_subagents.jsonl"


def _replay(events, t0=1000.0):
    run = agents.AgentRun("req-1", "sess-1")
    run.start_main("Claude Code", "claude", now=t0)
    for i, ev in enumerate(events):
        run.apply_claude(ev, now=t0 + i)
    return run


# ---- agents: real stream-json capture (2 parallel subagents) ---------------------------------

def test_real_stream_two_subagents():
    run = _replay(agents.load_events(FIX))
    main, *subs = run.agents
    assert main["name"] == "Claude Code" and main["parent"] is None
    assert main["status"] == "done" and main["model"] == "claude-haiku-5-5"
    assert main["duration_s"] and main["tokens"] > 0 and main["output_tokens"] > 0
    assert [s["name"] for s in subs] == ["Reply with alpha", "Reply with beta"]
    for s in subs:
        assert s["parent"] == main["id"]
        assert s["status"] == "done" and s["description"] == "general-purpose"
        assert s["tokens"] > 30000            # tool_use_result.totalTokens wins over progress figures
        assert 2 < s["duration_s"] < 5        # totalDurationMs
    assert subs[0]["tokens"] == 32274 and subs[0]["duration_s"] == 2.9
    summ = run.summary()
    assert summ["agents"] == 3 and summ["running"] == 0 and summ["tokens"] == sum(a["tokens"] for a in run.agents)


def test_running_state_mid_stream():
    evs = agents.load_events(FIX)
    cut = next(i for i, e in enumerate(evs) if e.get("subtype") == "task_progress")
    run = _replay(evs[: cut + 1])
    main, a, b = run.agents
    assert main["status"] == "running" and a["status"] == "running" and b["status"] == "running"
    assert a["tokens"] == 32182 and a["step"] == "Using SubagentHandback"
    assert run.summary()["running"] == 3 and run.summary()["duration_s"] is None


def _assistant(content, *, mid="m1", parent=None, usage=None):
    return {"type": "assistant", "parent_tool_use_id": parent,
            "message": {"id": mid, "content": content, "usage": usage or {}}}


def test_failed_subagent_and_dedup_usage():
    u = {"input_tokens": 10, "cache_read_input_tokens": 100, "output_tokens": 5}
    evs = [
        {"type": "system", "subtype": "init", "model": "claude-x"},
        _assistant([{"type": "tool_use", "id": "t1", "name": "Task",
                     "input": {"description": "Scan repo", "subagent_type": "Explore"}}], usage=u),
        _assistant([{"type": "text", "text": "Same message, second block"}], usage=u),  # same id: no double count
        _assistant([{"type": "tool_use", "id": "x", "name": "Grep", "input": {"pattern": "foo"}}],
                   mid="s1", parent="t1", usage={"input_tokens": 50, "output_tokens": 7}),
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "is_error": True,
                                                  "content": "boom"}]}},
        {"type": "result", "subtype": "error_during_execution", "is_error": True, "duration_ms": 9000},
    ]
    run = _replay(evs)
    main, sub = run.agents
    assert main["output_tokens"] == 5 and main["tokens"] == 115
    assert sub["name"] == "Scan repo" and sub["description"] == "Explore"
    assert sub["status"] == "failed" and sub["tokens"] == 57 and sub["step"] == "Failed"
    assert main["status"] == "failed" and main["duration_s"] == 9.0


def test_steps_follow_describe_claude_event():
    run = _replay([_assistant([{"type": "tool_use", "id": "a", "name": "Read",
                                "input": {"file_path": "/x/y/app.py"}}])])
    assert run.agents[0]["step"] == "Reading app.py"
    run.apply_claude(_assistant([{"type": "thinking", "thinking": "long thoughts"}], mid="m2"), now=2000)
    assert run.agents[0]["step"] == "Thinking"


def test_codex_events_astra():
    run = agents.AgentRun("req-2", "sess-1")
    run.start_main("Astra · planning", "astra", now=0)
    for ev in [{"type": "thread.started", "thread_id": "th-1"}, {"type": "turn.started"},
               {"type": "item.started", "item": {"type": "command_execution", "command": "ls -la"}},
               {"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}},
               {"type": "turn.completed", "usage": {"input_tokens": 16590, "cached_input_tokens": 12288,
                                                    "output_tokens": 5}}]:
        run.apply_codex(ev, now=1)
    run.finish_main(True, now=12.5)
    a = run.agents[0]
    assert a["thread_id"] == "th-1" and a["tool_uses"] == 1 and a["tokens"] == 16595
    assert a["status"] == "done" and a["duration_s"] == 12.5 and a["step"] == "ok"


def test_waiting_and_multiple_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(agents, "REQUESTS_DIR", tmp_path / "requests")
    monkeypatch.setattr(agents, "AGENTS_INDEX_DIR", tmp_path / "agents")
    (tmp_path / "requests" / "r1").mkdir(parents=True)
    (tmp_path / "requests" / "r1" / "request.json").write_text(json.dumps(
        {"id": "r1", "status": "awaiting_answer", "original_prompt": "do it", "route": "claude"}), encoding="utf-8")
    run = agents.AgentRun("r1", "s1")
    run.start_main("Claude Code", "claude", now=0)
    run.finish_main(True, waiting=True, now=5)
    run.save(force=True)
    run2 = agents.AgentRun.load("r1", "s1")
    m2 = run2.start_main("Claude Code · answered", "claude", now=10)
    assert m2["id"] == "claude-2" and run2.agents[0]["status"] == "done" and run2.agents[0]["step"] == "Answered"
    run2.finish_main(True, now=20)
    run2.save(force=True)
    view = agents.session_view("s1")
    assert [r["id"] for r in view["requests"]] == ["r1"]
    row = view["requests"][0]
    assert row["status"] == "awaiting_answer" and row["agents"] == 2 and row["prompt"] == "do it"
    assert [a["id"] for a in row["agents_list"]] == ["claude-1", "claude-2"]


# ---- questions ---------------------------------------------------------------------------------

BLOCK = """Done: looked at the repo.

```hermes-question
{"questions": [{"header": "Theme", "question": "Which colour scheme?", "multiSelect": false,
  "options": [{"label": "Dark", "description": "Matches the app"}, {"label": "Light", "description": "Brighter"}]},
 {"header": "Pages", "question": "Which pages?", "multiSelect": true, "options": ["Home", "Pricing", "Docs"]}]}
```
"""


def test_extract_block():
    clean, q = questions.extract(BLOCK)
    assert clean == "Done: looked at the repo."
    assert [x["header"] for x in q["questions"]] == ["Theme", "Pages"]
    assert q["questions"][0]["options"][0] == {"label": "Dark", "description": "Matches the app"}
    assert q["questions"][1]["multiSelect"] is True
    assert [o["label"] for o in q["questions"][1]["options"]] == ["Home", "Pricing", "Docs"]


def test_extract_none_and_invalid():
    assert questions.extract("Done.") == ("Done.", None)
    assert questions.extract("```hermes-question\nnot json\n```")[1] is None
    assert questions.extract('```hermes-question\n{"questions": []}\n```')[1] is None
    assert questions.extract("```json\n{\"questions\": [{\"question\": \"x?\"}]}\n```")[1] is None


def test_normalize_limits_and_shapes():
    many = {"questions": [{"question": f"q{i}?", "header": "A very long header indeed",
                           "options": [f"o{j}" for j in range(6)]} for i in range(6)]}
    q = questions.normalize(many)
    assert len(q["questions"]) == 4 and all(len(x["options"]) == 4 for x in q["questions"])
    assert len(q["questions"][0]["header"]) <= 16
    single = questions.normalize({"question": "Ship it?", "options": ["Yes", "No"]})
    assert single["questions"][0]["header"] == "Question 1"
    assert questions.normalize(["Plain string question?"])["questions"][0]["options"] == []


def test_block_roundtrip_and_answer_prompt():
    _, q = questions.extract(BLOCK)
    b = questions.block(q, request_id="r9", source="claude")
    payload = json.loads(b.split("\n", 1)[1].rsplit("\n", 1)[0])
    assert payload["request_id"] == "r9" and payload["source"] == "claude"
    assert questions.extract("x\n" + b)[1]["questions"] == q["questions"]
    p = questions.answer_prompt(q, "Theme: Dark\nPages: Home, Docs")
    assert "[Theme] Which colour scheme?" in p and "Pages: Home, Docs" in p


def test_planner_schema_and_worker_rules():
    item = providers.PLAN_SCHEMA["properties"]["questions"]["items"]
    assert item["required"] == ["header", "question", "multiSelect", "options"]
    assert item["additionalProperties"] is False
    rules = providers.WORKER_RULES.format(max_subagents=2)
    assert "```hermes-question" in rules and '{"questions": [' in rules


# ---- sidecar -----------------------------------------------------------------------------------

def _sidecar():
    spec = importlib.util.spec_from_file_location("cc_sidecar", PKG / "sidecar.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def test_sidecar_question_rounds():
    sc = _sidecar()
    planner_q = json.dumps({"kind": "questions", "questions": []})
    req = {"route": "astra", "status": "awaiting_answers", "planning_transcript": [
        {"role": "planner", "content": planner_q}, {"role": "user", "content": "Dark"},
        {"role": "planner", "content": planner_q}]}
    rounds = sc.question_rounds(req)
    assert [(r["round"], r["answer"], r["pending"]) for r in rounds] == [(1, "Dark", False), (2, None, True)]
    req = {"route": "claude", "status": "awaiting_answer",
           "question_log": [{"round": 1, "source": "claude", "answer": "A"}],
           "question": {"round": 2, "source": "claude", "questions": [{"question": "x?"}]}}
    rounds = sc.question_rounds(req)
    assert [(r["round"], r["pending"]) for r in rounds] == [(1, False), (2, True)]


def test_sidecar_token_fails_closed(tmp_path, monkeypatch):
    sc = _sidecar()
    monkeypatch.setattr(sc, "TOKEN_FILE", tmp_path / "missing.token")
    assert not sc._token_ok("anything")
    tok = tmp_path / "t.token"; tok.write_text("s3cret-token-value-123\n", encoding="utf-8")
    monkeypatch.setattr(sc, "TOKEN_FILE", tok)
    assert sc._token_ok("s3cret-token-value-123") and not sc._token_ok("wrong") and not sc._token_ok(None)


def test_clean_title():
    import importlib.util, pathlib
    spec = importlib.util.spec_from_file_location("cc_sidecar_t", pathlib.Path(__file__).parent.parent / "plugin/command-centre/sidecar.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    assert m.clean_title('Title: "Apple-Style Internet Money Motion."\n') == "Apple-Style Internet Money Motion"
    assert m.clean_title("\n\n**Fix Greeting Size**") == "Fix Greeting Size"
    assert m.clean_title("") == ""
    assert len(m.clean_title("x" * 200)) == 48
    # never more than four words; a dangling joiner is dropped
    assert m.clean_title("Landlord Email Choice For Smart Import Rightmove") == "Landlord Email Choice For".rsplit(" ", 1)[0]
    assert m.clean_title("Android Port From iOS App") == "Android Port From iOS"
    assert m.clean_title("Tenancy Rules Explained") == "Tenancy Rules Explained"
    # Haiku echoing our wrapper, or a title built from envelope text, keeps only the user's words
    assert m.clean_title("<message>\n/limit\n</message>") == "/limit"
    assert m.clean_title('<pasted_content id="90a5"> Web3 Audit Leads') == "Web3 Audit Leads"


def test_title_input_strips_envelope_markup():
    import importlib.util, pathlib
    spec = importlib.util.spec_from_file_location("cc_sidecar_e", pathlib.Path(__file__).parent.parent / "plugin/command-centre/sidecar.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    se = m.strip_envelope
    assert se("<message> /limit </message>") == "/limit"
    assert se('<pasted_content id="2e84">\nBuild my command centre\n</pasted_content>') == "Build my command centre"
    assert se("pasted_content id=f0d2 Plan the campaign") == "Plan the campaign"
    assert se("<command-message>motion</command-message>\n<command-name>/motion</command-name>\n"
              "<command-args>apple style NBA promo</command-args>") == "/motion\napple style NBA promo"
    assert se("Summarise this @file:/tmp/composer-pastes/pasted_content_1.txt\n\n--- Attached Context ---\n"
              "### file: pasted_content.txt\nlots of text") == "Summarise this"
    assert se("<system-reminder>ignore me</system-reminder>fix the <div> layout") == "fix the <div> layout"


def test_make_title_sends_clean_text_and_saves_no_session(monkeypatch):
    import importlib.util, pathlib, subprocess, types
    spec = importlib.util.spec_from_file_location("cc_sidecar_m", pathlib.Path(__file__).parent.parent / "plugin/command-centre/sidecar.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    calls = []
    monkeypatch.setattr(m.agent_set, "has", lambda _a: True)
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: calls.append((cmd, k["input"])) or
                        types.SimpleNamespace(stdout="Check Usage Limits\n"))
    assert m.make_title('<pasted_content id="ab12">check my limits</pasted_content>') == "Check Usage Limits"
    cmd, sent = calls[-1]
    assert "--no-session-persistence" in cmd
    assert sent == "<message>\ncheck my limits\n</message>"


def test_supersede_waiting_marks_older_asking_run_answered():
    ags = [agents.new_agent("claude-1", "Claude Code"), agents.new_agent("t1", "tool", parent="claude-1"),
           agents.new_agent("claude-2", "Claude Code")]
    ags[0]["status"] = "waiting"
    agents.supersede_waiting(ags)
    assert ags[0]["status"] == "done" and ags[0]["step"] == "Answered"
    one = [agents.new_agent("claude-1", "Claude Code")]
    one[0]["status"] = "waiting"
    assert agents.supersede_waiting(one)[0]["status"] == "waiting"  # still the latest run: keep waiting


# ---- speed plan: background tasks, hand-off rows, Codex sub-agents, stopped ----------------------

def test_background_task_is_not_a_subagent():
    run = _replay([
        {"type": "system", "subtype": "task_started", "tool_use_id": "bg1", "task_id": "b1",
         "description": "Wait 50 seconds in background", "task_type": "local_bash"},
        {"type": "system", "subtype": "task_notification", "tool_use_id": "bg1", "task_id": "b1", "status": "completed"},
        {"type": "result", "subtype": "success", "duration_ms": 5000, "num_turns": 2}])
    main, bg = run.agents
    assert bg["kind"] == "background" and bg["status"] == "done" and bg["name"] == "Wait 50 seconds in background"
    s = run.summary()
    assert s["agents"] == 1 and s["running"] == 0  # a background shell task is not an agent



def _bash_bg(tid, task):
    """A real `run_in_background` Bash call: task_started, then an instant tool_result ack."""
    return [{"type": "system", "subtype": "task_started", "tool_use_id": tid, "task_id": task, "is_backgrounded": True,
             "description": "Sleep 20 seconds then echo hi", "task_type": "local_bash"},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": tid,
             "content": f"Command running in background with ID: {task}."}]},
             "tool_use_result": {"backgroundTaskId": task}}]


def test_background_task_stays_running_until_it_ends():
    # shape from a real capture: the turn ends (result) while the task runs, Claude wakes on its notification
    # (a second init) and answers, then a second result
    run = _replay([*_bash_bg("bg1", "b1"),
                   {"type": "result", "subtype": "success", "duration_ms": 3000, "num_turns": 2}])
    main, bg = run.agents
    assert bg["status"] == "running" and main["status"] == "running"
    assert main["step"] == "Waiting for background tasks"
    for ev in [{"type": "system", "subtype": "task_notification", "tool_use_id": "bg1", "task_id": "b1",
                "status": "completed"},
               {"type": "system", "subtype": "init", "model": "claude-haiku"},
               _assistant([{"type": "text", "text": "It printed hi."}], mid="m9"),
               {"type": "result", "subtype": "success", "duration_ms": 2000, "num_turns": 1}]:
        run.apply_claude(ev, now=1100.0)
    assert bg["status"] == "done" and main["status"] == "done"
    assert main["duration_s"] == 100.0  # the whole run, not the last turn's 2s


def test_background_list_settles_tasks_without_a_notification():
    run = _replay([*_bash_bg("bg1", "b1"),
                   {"type": "system", "subtype": "background_tasks_changed", "tasks": []}])
    assert run.agents[1]["status"] == "done"


def test_activity_after_a_result_reopens_the_main_agent():
    run = _replay([{"type": "result", "subtype": "success", "duration_ms": 1000, "num_turns": 1},
                   {"type": "system", "subtype": "init", "model": "claude-haiku"}])
    assert run.main()["status"] == "running" and run.main()["ended"] is None
    run.finish_main(True, stopped=True)
    run.apply_claude({"type": "system", "subtype": "init"}, now=2000.0)
    assert run.main()["status"] == "stopped"  # Stop is final

def test_real_subagents_keep_kind_subagent():
    run = _replay(agents.load_events(FIX))
    assert [a.get("kind") for a in run.agents] == [None, "subagent", "subagent"]


def test_delegate_rows_merge_into_the_session_view(tmp_path, monkeypatch):
    monkeypatch.setattr(agents, "REQUESTS_DIR", tmp_path / "req")
    monkeypatch.setattr(agents, "AGENTS_INDEX_DIR", tmp_path / "idx")
    rid = "20261102-120000-abc123"
    (tmp_path / "req" / rid).mkdir(parents=True)
    run = agents.AgentRun(rid, "sess-9")
    run.start_main("Claude Code", "claude", now=1.0)
    run.save(force=True)
    d = agents.AgentRun(rid, "sess-9")
    d.file_name, d.index = "agents-1200-ab12.json", False
    row = d.start_main("Codex GPT-6 Astra · review", "astra", now=2.0)
    row.update(id="delegate-1200-ab12", parent="claude-1", kind="delegate", to="codex", tokens=1500)
    d._main = row["id"]
    d.apply_codex({"type": "item.started", "item": {"type": "collab_tool_call", "tool": "spawn_agent",
                                                    "receiver_thread_ids": ["th-sub"], "prompt": "check tests"}}, now=3.0)
    d.save(force=True)
    view = agents.session_view("sess-9")["requests"][0]
    ids = [(a["id"], a["parent"]) for a in view["agents_list"]]
    assert ids == [("claude-1", None), ("delegate-1200-ab12", "claude-1"), ("th-sub", "delegate-1200-ab12")]
    assert view["agents"] == 3 and view["tokens"] >= 1500


def test_codex_collab_fixture_and_spawn_rows():
    run = agents.AgentRun("r", "s")
    run.start_main("Codex", "astra", now=0.0)
    for ev in agents.load_events(Path(__file__).parent / "fixtures" / "codex-collab-wait.jsonl"):
        run.apply_codex(ev, now=1.0)
    assert len(run.agents) == 1 and run.agents[0]["tokens"] > 0  # a wait with no agents adds no row
    run.apply_codex({"type": "item.completed", "item": {"type": "collab_tool_call", "tool": "spawn_agent",
                                                        "receiver_thread_ids": ["t2"], "prompt": "reply PONG",
                                                        "status": "completed"}}, now=2.0)
    run.apply_codex({"type": "item.completed", "item": {"type": "collab_tool_call", "tool": "wait",
                                                        "agents_states": {"t2": {"status": "completed"}}}}, now=5.0)
    sub = run.get("t2")
    assert sub["parent"] == run.agents[0]["id"] and sub["kind"] == "subagent" and sub["status"] == "done"
    assert sub["name"] == "reply PONG"


def test_stopped_is_not_failed():
    run = agents.AgentRun("r", "s")
    run.start_main("Claude Code", "claude", now=0.0)
    run.finish_main(False, stopped=True, now=3.0)
    a = run.agents[0]
    assert a["status"] == "stopped" and a["step"] == "Stopped by you"
    s = run.summary()
    assert s["failed"] == 0 and s["stopped"] == 1
