"""Early start (P3), planner prebrief (P6), limit switch offers and the planner fallback."""
from __future__ import annotations
import json, threading, time

import pytest

from cc_helpers import cc, gate, prov, request, st, wire


@pytest.fixture()
def early(tmp_path, monkeypatch):
    wire(tmp_path, monkeypatch, {"executor": {"early_start": True}, "router": {"plan_threshold": 3}})
    monkeypatch.setattr(cc, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(cc, "_JOBS", {})
    proj = tmp_path / "proj"; proj.mkdir()
    monkeypatch.setattr(cc.projects, "registry_for", lambda *a, **k: None)
    monkeypatch.setattr(cc.projects, "resolve", lambda text, cwd: (str(proj), "workspace"))
    monkeypatch.setattr(cc.projects, "folders_for", lambda p: (p, []))
    monkeypatch.setattr(cc.projects, "expand_with_project", lambda sid, p, x: (p, x))
    monkeypatch.setattr(cc, "_session_cwd", lambda: str(proj))
    return tmp_path


def _counting(monkeypatch, gate_ev=None):
    calls = []

    def fake(r, feed=None, force=None):
        calls.append(r.id)
        if feed:
            feed("Claude Code started")
        if gate_ev:
            gate_ev.wait(5)
        return {"request_id": r.id, "ok": True, "route": "Route: x", "claude_result": "Done."}
    monkeypatch.setattr(cc, "_execute", fake)
    return calls


def test_starts_on_direct_route_and_tool_attaches_once(early, monkeypatch):
    go = threading.Event()
    calls = _counting(monkeypatch, go)
    d = cc._route_turn("s1", "fix the typo in README")
    assert "cc_claude_execute" in d
    rid = d.split('request_id="')[1].split('"')[0]
    time.sleep(0.1)
    assert calls == [rid] and rid in cc._JOBS  # running before the hub's first call returns
    lines = []
    monkeypatch.setattr(cc, "_live_feed", lambda: lines.append)
    go.set()
    out = json.loads(cc._tool_claude_execute({"request_id": rid}))
    assert out["claude_result"] == "Done." and calls == [rid]  # no double run
    assert lines == ["Claude Code started"]  # the buffered live line reached the tool's feed
    assert rid not in cc._JOBS


def test_not_started_for_planning_ask_project_or_hub_tools(early, monkeypatch):
    calls = _counting(monkeypatch)
    assert "cc_astra_plan" in cc._route_turn("s2", "@plan redesign the whole app")
    assert "workboard" in cc._route_turn("s3", "add a todo: buy milk")
    monkeypatch.setattr(cc.projects, "resolve", lambda text, cwd: (None, "none"))
    monkeypatch.setattr(cc, "_session_cwd", lambda: None)
    assert "which project" in cc._route_turn("s4", "fix the login bug")
    time.sleep(0.1)
    assert calls == [] and cc._JOBS == {}


def test_off_by_config(early, monkeypatch):
    calls = _counting(monkeypatch)
    monkeypatch.setattr(cc, "load_config", lambda: {"executor": {"early_start": False}})
    cc._route_turn("s5", "fix the typo in README")
    time.sleep(0.05)
    assert calls == [] and cc._JOBS == {}


def test_stop_cancels_unattached_job(early, monkeypatch):
    go = threading.Event()
    _counting(monkeypatch, go)
    d = cc._route_turn("s6", "fix the typo in README")
    rid = d.split('request_id="')[1].split('"')[0]
    cc._on_agent_loop_stopped(session_key="agent:main:s6", reason="stop")
    assert (st.Request(rid).dir / prov.STOP_MARKER).exists()
    go.set()


# ---- P6 planner prebrief ------------------------------------------------------------------------

def _approved(mods=None, brief="PREBRIEF: do X"):
    r = request("plan it", route="astra")
    r["proposal"] = {"kind": "proposal", "proposal_markdown": "p", "brief_markdown": brief, "specialists": []}
    r["status"] = "approved"
    r["modifications"] = mods or []
    r.save()
    return r


def test_unchanged_approval_skips_finalize(early, monkeypatch):
    planner = []
    monkeypatch.setattr(cc, "_planner_run", lambda *a, **k: planner.append(a[2]) or prov.PlanResult(
        True, {"kind": "brief", "brief_markdown": "FINAL", "changed_points": ["c"], "specialists": []}, "codex-cli", "m"))
    monkeypatch.setattr(cc, "_execute", lambda r, **k: {"request_id": r.id, "ok": True})
    r = _approved()
    json.loads(cc._tool_execute_approved({"request_id": r.id}))
    assert planner == [] and st.Request(r.id)["brief"] == "PREBRIEF: do X"
    r2 = _approved(mods=["but use blue"])
    out = json.loads(cc._tool_execute_approved({"request_id": r2.id}))
    assert planner == ["finalize"] and st.Request(r2.id)["brief"] == "FINAL" and out["changed_points"] == ["c"]
    r3 = _approved(brief="")  # an older proposal without a brief: finalize as before
    cc._tool_execute_approved({"request_id": r3.id})
    assert planner == ["finalize", "finalize"]
    monkeypatch.setattr(cc, "load_config", lambda: {"planner": {"finalize": "always"}})
    cc._tool_execute_approved({"request_id": _approved().id})
    assert len(planner) == 3


def test_discover_prompt_asks_for_prebrief():
    req = {"planning_transcript": [], "original_prompt": "x", "project": None}
    assert "ALSO fill brief_markdown" in prov._planner_prompt("discover", req, "")
    assert "ALSO fill brief_markdown" not in prov._planner_prompt("discover", req, "", prebrief=False)
    assert "{prebrief}" not in prov._planner_prompt("revise", req, "")


# ---- limits gate wiring ---------------------------------------------------------------------------

def _blocked(agent_block):
    def check(agent, model=None, **k):
        if agent in agent_block:
            return gate.Gate(agent, "blocked", 97.0, "5h session", 1_800_000_000.0,
                             f"{'Claude' if agent == 'claude' else 'Codex'} is at 97% of its 5-hour window")
        return gate.Gate(agent, "ok", 10.0)
    return check


def test_selected_executor_at_limit_offers_the_other(early, monkeypatch):
    monkeypatch.setattr(gate, "check", _blocked({"claude"}))
    monkeypatch.setenv("TABBIE_LINKED", "claude,codex")
    ran = []
    monkeypatch.setattr(cc, "run_claude", lambda *a, **k: pytest.fail("nothing may spawn"))
    monkeypatch.setattr(cc, "run_astra_executor", lambda prompt, **k: ran.append(k) or {
        "ok": True, "result": "Done.", "model": k["model"], "billing": "chatgpt", "thread_id": "t"})
    r = request("fix it", project=early / "proj")
    out = cc._execute(r)
    assert out["ok"] is False and "Let GPT do this one?" in out["action"] and "Claude is at 97%" in out["action"]
    offer = st.session_state("s1")["takeover_offer"]
    assert offer == {"rid": r.id, "to": "codex", "kind": "switch", "model": "gpt-6-astra"}
    assert st.Request(r.id)["status"] == "limit_blocked"
    # the user's "yes" routes to cc_takeover, which runs just this request on GPT
    assert "cc_takeover" in cc._route_turn("s1", "yes")
    res = json.loads(cc._tool_takeover({"request_id": r.id, "accept": True}))
    assert res["ok"] and ran[-1]["model"] == "gpt-6-astra" and not st.session_state("s1").get("takeover")


def test_other_not_linked_gives_one_line(early, monkeypatch):
    monkeypatch.setattr(gate, "check", _blocked({"claude"}))
    monkeypatch.setenv("TABBIE_LINKED", "claude")
    out = cc._execute(request("fix it", project=early / "proj"))
    assert "action" not in out and "Claude is at 97%" in out["note"] and "Nothing was started" in out["note"]


def test_codex_pick_at_limit_offers_claude(early, monkeypatch):
    cc.executor_choice.set_choice("s7", "openai", "gpt-6.1-sol")
    monkeypatch.setattr(gate, "check", _blocked({"codex"}))
    monkeypatch.setenv("TABBIE_LINKED", "claude,codex")
    monkeypatch.setattr(cc, "run_astra_executor", lambda *a, **k: pytest.fail("nothing may spawn"))
    seen = []
    monkeypatch.setattr(cc, "run_claude", lambda prompt, **k: seen.append(k) or {"ok": True, "result": "Done.", "billing": "s"})
    r = request("fix it", sid="s7", project=early / "proj")
    out = cc._execute(r)
    assert "Let Claude do this one?" in out["action"]
    res = json.loads(cc._tool_takeover({"request_id": r.id, "accept": True}))
    assert res["ok"] and seen[-1]["model"] == "claude-sonnet-5-5"


def test_planner_falls_back_to_other_cli(early, monkeypatch):
    monkeypatch.setattr(gate, "check", _blocked({"codex"}))
    monkeypatch.setenv("TABBIE_LINKED", "claude,codex")
    used = []
    monkeypatch.setattr(cc, "run_planner", lambda phase, req, m, imgs, cfg, wd, on_event=None: used.append(
        prov.planner_config(cfg)["provider"]) or prov.PlanResult(True, {"kind": "proposal", "summary": "", "questions": [],
        "proposal_markdown": "P", "decisions": [], "brief_markdown": "B", "changed_points": [], "specialists": []},
        "claude-cli", "opus"))
    r = request("plan it", route="astra")
    out = json.loads(cc._tool_astra_plan({"request_id": r.id}))
    assert used == ["claude-cli"] and "Note: planned with Claude because Codex is at 97%" in out["display"]
    monkeypatch.setattr(gate, "check", _blocked({"codex", "claude"}))
    r2 = request("plan it", route="astra")
    out = json.loads(cc._tool_astra_plan({"request_id": r2.id}))
    assert out["ok"] is False and "Planning was not started" in out["error"]
