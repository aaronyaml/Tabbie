"""Dynamic agents: the hand-off gate, the setting, the rules, and the `delegate` MCP server."""
from __future__ import annotations
import importlib.util, io, json, sys
from pathlib import Path

import pytest

from cc_helpers import cc, ec, prov, wire

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "plugin" / "command-centre"


def _dm():
    spec = importlib.util.spec_from_file_location("cc_delegate_mcp_test", PKG / "delegate_mcp.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def _agents():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from hmem import agents
    return agents


# ---- the gate ---------------------------------------------------------------------------------

@pytest.mark.parametrize("linked,enabled,dynamic,depth,ok", [
    ("claude,codex", ["claude", "codex"], None, "0", True),     # default on
    ("claude,codex", ["claude", "codex"], False, "0", False),   # switched off
    ("claude", ["claude", "codex"], None, "0", False),          # Codex signed out
    ("codex", ["claude", "codex"], None, "0", False),           # Claude signed out
    ("claude,codex", ["claude"], None, "0", False),             # Codex not enabled
    ("claude,codex", ["claude", "codex"], None, "1", False),    # a delegate never hands off again
])
def test_gate_matrix(monkeypatch, linked, enabled, dynamic, depth, ok):
    a = _agents()
    a.save(enabled, dynamic=dynamic)
    monkeypatch.setenv("TABBIE_LINKED", linked)
    monkeypatch.setenv("TABBIE_DELEGATE_DEPTH", depth)
    assert prov.dynamic_gate()[0] is ok


def test_setting_default_on_and_round_trip():
    a = _agents()
    assert a.dynamic_enabled() is True  # no file
    a.path().parent.mkdir(parents=True, exist_ok=True)
    a.path().write_text("enabled: [claude, codex]\n", encoding="utf-8")
    assert a.dynamic_enabled() is True  # older file without the key
    a.save(None, dynamic=False)
    text = a.path().read_text(encoding="utf-8")
    assert "enabled: [claude, codex]" in text and "dynamic: false" in text and a.dynamic_enabled() is False
    a.save(["claude"])  # changing agents keeps the dynamic setting
    assert a.enabled() == ["claude"] and a.dynamic_enabled() is False
    a.save(None, dynamic=True)
    assert a.enabled() == ["claude"] and a.dynamic_enabled() is True
    assert a.dynamic_allowed() is False  # one agent: no hand-offs
    a.save(["claude", "codex"])
    assert a.dynamic_allowed({"claude": True, "codex": True}) and not a.dynamic_allowed({"claude": True, "codex": False})


def test_rule_variants_name_the_other_provider():
    c, x = prov.worker_rules("claude", dynamic=True), prov.worker_rules("codex", dynamic=True)
    assert "Codex (OpenAI GPT)" in c and "Agent/Task" in c and "Claude Code" not in c.split("DYNAMIC AGENTS")[1]
    assert "Claude Code (Anthropic Claude)" in x and "spawn_agent" in x
    for r in (c, x):
        assert "None of these is" in r and "Never for trivial steps" in r and "you own the final answer" in r


def test_peer_model_same_tier():
    assert ec.peer_model("anthropic", "claude-opus-5-5")["model"] == "gpt-6.1-sol"
    assert ec.peer_model("openai", "gpt-6.1-sol")["model"] == "claude-opus-5-5"
    assert ec.peer_model("anthropic", "claude-fable-5-1")["model"] == "gpt-6-astra"
    assert ec.peer_model("openai", "gpt-6-astra")["model"] == "claude-fable-5-2"
    for a, b in ec.PEER.items():
        assert ec.PEER[b] == a  # every pair goes both ways
    assert ec.peer_model("anthropic", "claude-haiku-5-5", available_openai=["gpt-6-astra"])["model"] == "gpt-6-astra"
    assert ec.peer_model("anthropic", "claude-opus-5-5", {"claude-opus-5-5": "gpt-6.1-terra"})["model"] == "gpt-6.1-terra"


def test_sidecar_agents_payload_has_dynamic_and_linked(monkeypatch):
    spec = importlib.util.spec_from_file_location("cc_sidecar_dyn", PKG / "sidecar.py")
    sc = importlib.util.module_from_spec(spec); spec.loader.exec_module(sc)
    monkeypatch.setenv("TABBIE_LINKED", "claude")
    p = sc.agents_payload()
    assert p["dynamic"] is True and p["linked"] == {"claude": True, "codex": False} and p["dynamic_available"] is False
    code, p = sc.set_agents({"dynamic": False})
    assert code == 200 and p["dynamic"] is False and p["enabled"] == ["claude", "codex"]
    monkeypatch.setenv("TABBIE_LINKED", "claude,codex")
    code, p = sc.set_agents({"dynamic": True})
    assert p["dynamic"] and p["dynamic_available"] is True


# ---- the delegate server ----------------------------------------------------------------------

@pytest.fixture()
def dm(tmp_path, monkeypatch):
    m = _dm()
    req = tmp_path / "req"
    rdir = req / "20261102-120000-abc123"
    rdir.mkdir(parents=True)
    (rdir / "request.json").write_text(json.dumps({"id": rdir.name, "original_prompt": "Build the parser " + "x" * 5000}), encoding="utf-8")
    for mod in (m.common, m.agents_mod):
        monkeypatch.setattr(mod, "REQUESTS_DIR", req)
    monkeypatch.setattr(m.agents_mod, "AGENTS_INDEX_DIR", tmp_path / "agents")
    monkeypatch.setattr(m.providers, "LOCKS_DIR", tmp_path / "locks")
    monkeypatch.setattr(m.common, "load_config", lambda: {"executor": {"max_parallel": 2, "worker_profile": "lean"}})
    monkeypatch.setattr(m.providers, "codex_models", lambda: ["gpt-6-astra", "gpt-6.1-sol"])
    monkeypatch.setattr(m.limits_gate, "log_decision", lambda **k: None)
    monkeypatch.setenv("TABBIE_LINKED", "claude,codex")
    monkeypatch.delenv("TABBIE_DELEGATE_DEPTH", raising=False)
    monkeypatch.setenv("TABBIE_REQUEST_DIR", str(rdir))
    monkeypatch.setenv("TABBIE_PARENT_AGENT", "claude-1")
    monkeypatch.setenv("TABBIE_DELEGATE_FROM", "claude")
    monkeypatch.setenv("TABBIE_CWD", str(tmp_path))
    monkeypatch.setenv("TABBIE_ADD_DIRS", "[]")
    monkeypatch.setenv("TABBIE_PARENT_MODEL", "claude-opus-5-5")
    monkeypatch.setenv("TABBIE_SESSION_ID", "s1")
    wpm = importlib.import_module("cc_delegate_pkg.worker_profile")  # never the real state/worker
    monkeypatch.setattr(wpm, "WORKER_DIR", tmp_path / "worker")
    monkeypatch.setattr(wpm, "SETTINGS_FILE", tmp_path / "worker" / "claude-settings.json")
    monkeypatch.setattr(wpm, "SKILLS_PLUGIN", tmp_path / "worker" / "user-skills")
    m.RDIR = rdir
    return m


def _codex_runner(seen):
    def run(cmd, *, cwd, env, timeout, stdin, on_line):
        seen.update(cmd=cmd, env=env, stdin=stdin)
        out = Path(cmd[cmd.index("-o") + 1])
        lines = [{"type": "thread.started", "thread_id": "t1"},
                 {"type": "item.completed", "item": {"type": "agent_message", "text": "Found 2 issues"}},
                 {"type": "turn.completed", "usage": {"input_tokens": 1200, "output_tokens": 300}}]
        for ev in lines:
            on_line(json.dumps(ev) + "\n")
        out.write_text("Found 2 issues: a, b", encoding="utf-8")
        return 0, "\n".join(json.dumps(e) for e in lines), ""
    return run


def test_delegate_runs_codex_with_bundle_row_and_result(dm):
    seen = {}
    d = dm.Delegator(runner=_codex_runner(seen))
    ok, text = d.call({"task": "Review parser.py for edge cases", "why": "independent review",
                       "context": "c" * 20000, "files": [f"f{i}.py" for i in range(50)], "mode": "read",
                       "expect": "bullets"})
    assert ok and text.startswith("Result from Codex GPT-6.1 Sol (read,") and "Found 2 issues: a, b" in text
    cmd, env, prompt = seen["cmd"], seen["env"], seen["stdin"]
    assert cmd[:2] == ["codex", "exec"] and cmd[cmd.index("-m") + 1] == "gpt-6.1-sol"
    assert cmd[cmd.index("-s") + 1] == "read-only" and "--ephemeral" in cmd and not any("tabbie" in c for c in cmd)
    assert env["TABBIE_DELEGATE_DEPTH"] == "1" and "OPENAI_API_KEY" not in env
    # the bundle: rules, folders, the user's request (capped), task, capped context, ≤30 files
    assert "You are a DELEGATE" in prompt and "Never ask the user anything" in prompt
    assert "THE USER'S ORIGINAL REQUEST" in prompt and "Build the parser" in prompt and "x" * 2100 not in prompt
    assert "Review parser.py for edge cases" in prompt and "c" * 8001 not in prompt and "c" * 7000 in prompt
    assert "- f29.py" in prompt and "- f30.py" not in prompt and "RETURN:\nbullets" in prompt
    # its own row file, nested under the calling worker, kind delegate
    rows = list(dm.RDIR.glob("agents-*.json"))
    assert len(rows) == 1
    a = json.loads(rows[0].read_text(encoding="utf-8"))["agents"][0]
    assert a["parent"] == "claude-1" and a["kind"] == "delegate" and a["to"] == "codex" and a["status"] == "done"
    assert a["name"].startswith("Codex GPT-6.1 Sol · Review parser.py") and a["tokens"] == 1500
    assert (dm.RDIR / "delegates").exists() and list((dm.RDIR / "delegates").glob("*/result.md"))
    merged = dm.agents_mod.merged_agents(dm.RDIR.name)
    assert [x["id"] for x in merged] == [a["id"]]


def test_codex_caller_delegates_to_claude(dm, monkeypatch):
    monkeypatch.setenv("TABBIE_DELEGATE_FROM", "codex")
    monkeypatch.setenv("TABBIE_PARENT_MODEL", "gpt-6.1-sol")
    monkeypatch.setattr(dm.providers, "_run_stream", None)  # must go through the runner
    seen = {}

    def run(cmd, *, cwd, env, timeout, stdin, on_line):
        seen.update(cmd=cmd, env=env)
        ev = {"type": "result", "subtype": "success", "result": "Rewrote the copy", "is_error": False,
              "usage": {"input_tokens": 10, "output_tokens": 90}}
        on_line(json.dumps(ev))
        return 0, json.dumps(ev), ""
    ok, text = dm.Delegator(runner=run).call({"task": "Polish the onboarding copy", "why": "writing", "mode": "write"})
    cmd = seen["cmd"]
    assert ok and "Rewrote the copy" in text and "Files changed:" in text
    assert cmd[0] == "claude" and "--no-session-persistence" in cmd and cmd[cmd.index("--model") + 1] == "claude-opus-5-5"
    assert "--strict-mcp-config" in cmd and "ANTHROPIC_API_KEY" not in seen["env"]
    rules = cmd[cmd.index("--append-system-prompt") + 1]
    assert "DYNAMIC AGENTS" not in rules  # the delegate gets no delegate tool and no hand-off rules


def test_delegate_refusals_write_notes(dm, monkeypatch):
    gate = dm.limits_gate
    monkeypatch.setattr(gate, "check", lambda *a, **k: gate.Gate("codex", "blocked", 96.0, "5h", None,
                                                                  "Codex is at 96% of its 5-hour window"))
    ok, text = dm.Delegator(runner=lambda *a, **k: pytest.fail("must not run")).call(
        {"task": "x", "why": "y", "mode": "read"})
    assert not ok and "96%" in text and "Do this part yourself" in text
    note = json.loads((dm.RDIR / "notes.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert note["kind"] == "delegate_refused" and note["reason"] == "limit"
    monkeypatch.setattr(gate, "check", lambda *a, **k: gate.Gate("codex", "ok"))

    class Busy:
        def __init__(self, *a): pass
        def __enter__(self): raise TimeoutError("busy")
        def __exit__(self, *a): pass
    monkeypatch.setattr(dm.providers, "WorkerSlot", Busy)
    ok, text = dm.Delegator(runner=lambda *a, **k: pytest.fail("must not run")).call({"task": "x", "why": "y", "mode": "read"})
    assert not ok and "busy" in text
    d = dm.Delegator(runner=lambda *a, **k: pytest.fail("must not run"))
    d.busy.acquire()
    ok, text = d.call({"task": "x", "why": "y", "mode": "read"})
    assert not ok and "One hand-off at a time" in text
    monkeypatch.setenv("TABBIE_DELEGATE_DEPTH", "1")
    ok, text = dm.Delegator().call({"task": "x", "why": "y", "mode": "read"})
    assert not ok and "cannot hand work off again" in text
    monkeypatch.setenv("TABBIE_DELEGATE_DEPTH", "0")
    monkeypatch.setenv("TABBIE_LINKED", "claude")
    ok, text = dm.Delegator().call({"task": "x", "why": "y", "mode": "read"})
    assert not ok and "signed in" in text


def test_mcp_handshake_and_tool_list(dm):
    inp = io.StringIO("\n".join(json.dumps(m) for m in [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "ping"}]) + "\n")
    out = io.StringIO()
    dm.serve(inp, out, dm.Delegator(runner=lambda *a, **k: (0, "", "")))
    msgs = [json.loads(x) for x in out.getvalue().splitlines()]
    assert msgs[0]["result"]["serverInfo"]["name"] == "tabbie" and msgs[0]["result"]["protocolVersion"] == "2025-06-18"
    tool = msgs[1]["result"]["tools"][0]
    assert tool["name"] == "delegate" and set(tool["inputSchema"]["required"]) == {"task", "why", "mode"}
    assert msgs[2] == {"jsonrpc": "2.0", "id": 3, "result": {}}


def test_execute_attaches_delegate_only_when_allowed(tmp_path, monkeypatch):
    from cc_helpers import request
    wire(tmp_path, monkeypatch, {"executor": {"worker_profile": "lean"}})
    calls = []
    monkeypatch.setattr(cc, "run_claude", lambda prompt, **k: calls.append(k) or {"ok": True, "result": "Done.", "billing": "sub"})
    proj = tmp_path / "p"; proj.mkdir()
    monkeypatch.setenv("TABBIE_LINKED", "claude,codex")
    cc._execute(request("one", project=proj))
    d = calls[-1]["delegate"]
    assert d and d["TABBIE_DELEGATE_FROM"] == "claude" and d["TABBIE_PARENT_AGENT"] == "claude-1"
    assert calls[-1]["profile"] == "lean"
    monkeypatch.setenv("TABBIE_LINKED", "claude")
    cc._execute(request("two", project=proj))
    assert calls[-1]["delegate"] is None
    _agents().save(None, dynamic=False)
    monkeypatch.setenv("TABBIE_LINKED", "claude,codex")
    cc._execute(request("three", project=proj))
    assert calls[-1]["delegate"] is None
