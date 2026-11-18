"""prep.py: a cheaper model reads ahead (chat digest, project scout) only when it pays off."""
from __future__ import annotations
import importlib.util, json, sys, tempfile
from importlib import import_module
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "plugin" / "command-centre"
if "command_centre" not in sys.modules:
    spec = importlib.util.spec_from_file_location("command_centre", PKG / "__init__.py",
                                                  submodule_search_locations=[str(PKG)])
    _m = importlib.util.module_from_spec(spec); sys.modules["command_centre"] = _m
    spec.loader.exec_module(_m)
cc = sys.modules["command_centre"]
st = import_module("command_centre.state")
prep = import_module("command_centre.prep")

LONG_ASK = "Add a cancel button to the booking form and make sure the API refunds the deposit properly"


def plan(**k):
    base = dict(worker="claude", provider="anthropic", model="claude-opus-5-5", message=LONG_ASK,
                conversation_chars=0, reopen_ctx=None, project=True)
    base.update(k)
    return prep.plan({}, **base)


def test_scout_for_real_work_in_a_project():
    p = plan()
    assert p == {"digest": False, "scout": True, "reopen": False, "model": "claude-sonnet-5-5"}


def test_quick_or_projectless_requests_skip_the_scout():
    assert plan(message="make it blue")["model"] is None
    assert plan(message="thanks!")["model"] is None
    assert plan(project=False)["scout"] is False


def test_cheap_pick_needs_no_prep():
    assert plan(model="claude-sonnet-5-5")["model"] is None
    assert plan(worker="codex", provider="openai", model="gpt-6-luna")["model"] is None


def test_openai_pick_uses_terra():
    assert plan(worker="codex", provider="openai", model="gpt-6.1-sol")["model"] == "gpt-6.1-terra"


def test_small_session_reopens_big_one_gets_a_digest():
    small = plan(reopen_ctx=12000)
    assert small["reopen"] and not small["digest"] and not small["scout"]  # the session already knows the project
    big = plan(reopen_ctx=60000)
    assert big["digest"] and not big["reopen"] and big["scout"]


def test_long_chat_without_a_session_gets_a_digest():
    assert plan(conversation_chars=20000, project=False) == {"digest": True, "scout": False, "reopen": False,
                                                              "model": "claude-sonnet-5-5"}


def test_shared_team_chat_history_is_digested_and_split():
    msg = "This message comes from the shared team chat “X”, started by Leo on their Mac. The conversation so far:\n\n" \
          + "**Leo:** " + "y" * 6000 + "\n\n---\n\n" + LONG_ASK
    assert prep.split_shared(msg)[1] == LONG_ASK
    assert plan(message=msg, project=False)["digest"] is True


def test_conversation_text_drops_the_current_request():
    msgs = [{"role": "user", "content": "a"}, {"role": "assistant", "content": [{"type": "text", "text": "b"}]},
            {"role": "tool", "content": "x"}, {"role": "user", "content": "now c"}]
    assert prep.conversation_text(msgs, "now c") == "USER: a\n\nASSISTANT: b"


def _setup(monkeypatch):
    d = Path(tempfile.mkdtemp())
    for mod, names in ((st, ("REQUESTS_DIR", "SESSIONS_DIR")),):
        monkeypatch.setattr(mod, "REQUESTS_DIR", d / "req"); monkeypatch.setattr(mod, "SESSIONS_DIR", d / "ses")
    monkeypatch.setattr(cc, "REQUESTS_DIR", d / "req")
    monkeypatch.setattr(cc, "LOCKS_DIR", d / "locks")
    monkeypatch.setattr(cc, "STATE_DIR", d)
    ag = import_module("command_centre.agents")
    monkeypatch.setattr(ag, "REQUESTS_DIR", d / "req")
    monkeypatch.setattr(ag, "AGENTS_INDEX_DIR", d / "agents")
    monkeypatch.setattr(cc, "log_decision", lambda **k: None)
    monkeypatch.setattr(cc, "load_config", lambda: {"executor": {}})
    monkeypatch.setattr(cc, "_CLAUDE_SESSIONS", d / "cs.json")
    monkeypatch.setattr(cc, "_memory_context", lambda r: "")
    monkeypatch.setenv("HERMES_WEBUI_STATE_DIR", str(d / "webui"))
    return d


def test_worker_gets_scout_notes_and_a_big_session_restarts_from_a_digest(monkeypatch):
    d = _setup(monkeypatch)
    proj = d / "proj"; proj.mkdir()
    (d / "webui" / "sessions").mkdir(parents=True)
    (d / "webui" / "sessions" / "chatP.json").write_text(json.dumps({"messages": [
        {"role": "user", "content": "earlier ask " * 50}, {"role": "assistant", "content": "earlier answer"}]}))
    asked = []
    monkeypatch.setattr(prep, "_ask", lambda worker, prompt, model, cwd, add_dirs, tools, timeout:
                        asked.append((model, tools, prompt[:40])) or ("- booking.py:10-40 form" if tools else "- user wants refunds"))
    calls = []
    monkeypatch.setattr(cc, "run_claude", lambda prompt, **k: calls.append((prompt, k)) or {
        "ok": True, "result": "Done.", "billing": "s", "session_id": f"S{len(calls)}"})
    r = st.Request.create(session_id="chatP", route="claude", reason="scoped", forced=False, prompt=LONG_ASK,
                          raw_message=LONG_ASK, project=str(proj), project_source="t")
    cc._execute(r)
    assert "PROJECT SCOUT NOTES" in calls[0][0] and "booking.py:10-40" in calls[0][0]
    assert all(m == "claude-sonnet-5-5" for m, *_ in asked)
    # the session grew big: the follow-up starts fresh from a digest instead of reopening it
    s = json.loads((d / "cs.json").read_text()); key = next(iter(s)); s[key]["ctx"] = 80000
    (d / "cs.json").write_text(json.dumps(s))
    r2 = st.Request.create(session_id="chatP", route="claude", reason="scoped", forced=False, prompt=LONG_ASK + " too",
                           raw_message=LONG_ASK + " too", project=str(proj), project_source="t")
    cc._execute(r2)
    assert calls[1][1]["resume_session"] is None
    assert "THE CONVERSATION SO FAR" in calls[1][0] and "user wants refunds" in calls[1][0]
    assert "RECENT CONVERSATION IN THIS CHAT" not in calls[1][0]


def test_prep_failure_still_runs_and_reopens(monkeypatch):
    d = _setup(monkeypatch)
    proj = d / "proj"; proj.mkdir()
    monkeypatch.setattr(prep, "_ask", lambda *a, **k: "")
    calls = []
    monkeypatch.setattr(cc, "run_claude", lambda prompt, **k: calls.append((prompt, k)) or {
        "ok": True, "result": "Done.", "billing": "s", "session_id": f"S{len(calls)}"})
    mk = lambda t: st.Request.create(session_id="chatQ", route="claude", reason="scoped", forced=False, prompt=t,
                                     raw_message=t, project=str(proj), project_source="t")
    cc._execute(mk(LONG_ASK))
    s = json.loads((d / "cs.json").read_text()); key = next(iter(s)); s[key]["ctx"] = 80000
    (d / "cs.json").write_text(json.dumps(s))
    cc._execute(mk(LONG_ASK + " again"))
    assert "PROJECT SCOUT NOTES" not in calls[0][0]
    assert calls[1][1]["resume_session"] == "S1"  # no digest came back: reopen the session after all
