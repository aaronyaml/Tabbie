"""Verbatim relay (P4): the worker's own reply replaces the hub's placeholder via transform_llm_output."""
from __future__ import annotations
import json

import pytest

from cc_helpers import cc, prov, request, st, wire


@pytest.fixture()
def relay(tmp_path, monkeypatch):
    wire(tmp_path, monkeypatch, {"executor": {"relay": "verbatim"}})
    monkeypatch.setitem(cc._RELAY, "on", True)
    monkeypatch.setattr(cc, "_PENDING", {})
    return tmp_path


def _run(monkeypatch, result, **extra):
    monkeypatch.setattr(cc, "run_claude", lambda prompt, **k: {"ok": True, "result": result, "billing": "sub", **extra})


def test_placeholder_replaced_with_worker_text(relay, monkeypatch):
    long = "Done: rewrote the parser\n" + "\n".join(f"- edge case {i}" for i in range(200)) + "\nVerified: 41 tests pass"
    _run(monkeypatch, long)
    proj = relay / "p"; proj.mkdir()
    r = request("fix the parser", project=proj)
    out = cc._execute(r)
    assert set(out) >= {"reply", "route", "ok"} and "claude_result" not in out  # the hub never sees the long text
    assert out["reply"].startswith(f"[[cc:reply {r.id}:")
    hub = f"{out['route']}\n\n{out['reply']}"
    shown = cc._on_transform_llm_output(response_text=hub, session_id="s1")
    assert shown.startswith(out["route"]) and long in shown and "[[cc:reply" not in shown


def test_unrelated_text_left_alone(relay):
    assert cc._on_transform_llm_output(response_text="Hello there", session_id="nobody") is None
    assert cc._on_transform_llm_output(response_text=None, session_id="x") is None


def test_missing_reply_file_leaves_text(relay):
    t = "Route: x\n\n[[cc:reply 20261102-120000-abcdef:deadbeef]]"
    assert cc._on_transform_llm_output(response_text=t, session_id="s1") is None


def test_hub_dropped_placeholder_still_shows_reply(relay, monkeypatch):
    _run(monkeypatch, "Done: it works")
    proj = relay / "p"; proj.mkdir()
    cc._execute(request("x", project=proj))
    shown = cc._on_transform_llm_output(response_text="Route: Claude Opus 5.5 — scoped", session_id="s1")
    assert shown.endswith("Done: it works")
    assert cc._on_transform_llm_output(response_text="Route: again", session_id="s1") is None  # consumed once


def test_failure_stop_and_notes(relay, monkeypatch):
    proj = relay / "p"; proj.mkdir()
    monkeypatch.setattr(cc, "run_claude", lambda prompt, **k: {"ok": False, "error": "boom: exit 1", "result": "",
                                                                "billing": "sub"})
    out = cc._execute(request("x", project=proj))
    body = cc._on_transform_llm_output(response_text=out["reply"], session_id="s1")
    assert body.startswith("Problem: boom")
    monkeypatch.setattr(cc, "run_claude", lambda prompt, **k: {"ok": False, "error": "stopped by you",
                                                                "stopped_by_user": True, "billing": "sub"})
    r = request("y", project=proj)
    out = cc._execute(r)
    assert out.get("stopped") and "Stopped by you" in cc._on_transform_llm_output(response_text=out["reply"], session_id="s1")
    assert st.Request(r.id)["status"] == "stopped"
    agents = json.loads((r.dir / "agents.json").read_text(encoding="utf-8"))["agents"]
    assert agents[0]["status"] == "stopped" and agents[0]["step"] == "Stopped by you"

    r2 = request("z", project=proj)
    (r2.dir / "notes.jsonl").write_text(json.dumps({"at": 9e9, "kind": "delegate_refused", "reason": "limit",
                                                    "note": "Codex is at 96% of its 5-hour window"}) + "\n", encoding="utf-8")
    _run(monkeypatch, "Done: did it myself")
    out = cc._execute(r2)
    body = cc._on_transform_llm_output(response_text=out["reply"], session_id="s1")
    assert body.splitlines()[0] == "Done: did it myself" and "Note: hand-off skipped: Codex is at 96%" in body


def test_relay_off_keeps_old_tool_result(relay, monkeypatch):
    monkeypatch.setitem(cc._RELAY, "on", False)
    _run(monkeypatch, "Done.")
    proj = relay / "p"; proj.mkdir()
    out = cc._execute(request("x", project=proj))
    assert out["claude_result"] == "Done." and "reply" not in out
    monkeypatch.setitem(cc._RELAY, "on", True)
    monkeypatch.setattr(cc, "load_config", lambda: {"executor": {"relay": "hub"}})
    assert "reply" not in cc._execute(request("y", project=proj))


def test_planner_display_path(relay, monkeypatch):
    r = request("plan the redesign", route="astra")
    data = {"kind": "proposal", "summary": "s", "questions": [], "proposal_markdown": "**Do X** then Y",
            "decisions": ["Pick A"], "brief_markdown": "BRIEF", "changed_points": [], "specialists": []}
    monkeypatch.setattr(cc, "_planner_run", lambda *a, **k: prov.PlanResult(True, data, "codex-cli", "gpt-6-astra"))
    out = json.loads(cc._tool_astra_plan({"request_id": r.id}))
    assert "reply" in out and "display" not in out
    shown = cc._on_transform_llm_output(response_text="Route: x\n\n" + out["reply"], session_id="s1")
    assert "**Do X** then Y" in shown and "- Pick A" in shown and "Reply **yes / go** to approve" in shown


def test_register_detects_hold_stream(monkeypatch):
    hooks = {}

    class NewCtx:
        def register_hook(self, name, fn, hold_stream=False):
            hooks[name] = hold_stream
        register_tool = register_command = lambda self, *a, **k: None

    class OldCtx:
        def register_hook(self, name, fn):
            hooks[name] = None
        register_tool = register_command = lambda self, *a, **k: None
    monkeypatch.setattr(cc, "log_decision", lambda **k: None)
    monkeypatch.setattr(cc, "_CTX", None)  # restored after the test
    monkeypatch.setitem(cc._RELAY, "on", False)
    cc.register(NewCtx())
    assert cc._RELAY["on"] is True and hooks["transform_llm_output"] is True
    assert {"agent_loop_stopped", "pre_api_request", "post_api_request"} <= set(hooks)
    cc.register(OldCtx())
    assert cc._RELAY["on"] is False  # older Hermes: today's relay
