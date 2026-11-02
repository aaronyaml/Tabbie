"""Chat continuity (per-chat resume, chat folder, recent turns), briefs sent as files, unfinished background work."""
from __future__ import annotations
import importlib.util, json, shutil, sys, tempfile
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
router = import_module("command_centre.router")


def _setup(monkeypatch):
    d = Path(tempfile.mkdtemp())
    monkeypatch.setattr(st, "REQUESTS_DIR", d / "req")
    monkeypatch.setattr(st, "SESSIONS_DIR", d / "ses")
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
    return d


def _req(sid, text, project=None):
    return st.Request.create(session_id=sid, route="claude", reason="scoped", forced=False, prompt=text,
                             raw_message=text, project=str(project) if project else None, project_source="t")


def test_worker_sessions_resume_per_chat_not_per_project(monkeypatch):
    d = _setup(monkeypatch)
    proj = d / "proj"; proj.mkdir()
    calls = []
    monkeypatch.setattr(cc, "run_claude", lambda prompt, **k: calls.append((prompt, k)) or {
        "ok": True, "result": "Done.", "billing": "s", "session_id": f"S{len(calls)}"})
    cc._execute(_req("chatA", "build the page", proj))
    cc._execute(_req("chatA", "make it blue", proj))
    cc._execute(_req("chatB", "unrelated test", proj))
    assert calls[0][1]["resume_session"] is None
    assert calls[1][1]["resume_session"] == "S1"           # same chat: continues its own session
    assert calls[2][1]["resume_session"] is None           # another chat never inherits it


def test_chat_without_project_keeps_its_folder_and_recent_turns(monkeypatch):
    d = _setup(monkeypatch)
    s = st.session_state("chatC")
    s["recent"] = [{"role": "user", "content": "make a puppy name list"},
                   {"role": "assistant", "content": "Here are 10 names: Biscuit, …"},
                   {"role": "user", "content": "make it longer"}]
    st.save_session_state("chatC", s)
    seen = []
    monkeypatch.setattr(cc, "run_claude", lambda prompt, **k: seen.append((prompt, k)) or {
        "ok": True, "result": "Done.", "billing": "s"})  # no session id: nothing to resume
    cc._execute(_req("chatC", "make it longer"))
    cc._execute(_req("chatC", "now sort them"))
    assert seen[0][1]["cwd"] == seen[1][1]["cwd"] == str((d / "chat-work" / "chatC").resolve())
    prompt = seen[0][0]
    assert "RECENT CONVERSATION IN THIS CHAT" in prompt and "puppy name list" in prompt
    assert prompt.count("make it longer") == 1  # the request itself, not repeated as history
    assert "this chat's own folder" in prompt


def test_brief_sent_as_a_file_counts_for_routing_and_effort(monkeypatch, tmp_path):
    home = Path.home() / ".cache" / "tabbie-test-brief"
    home.mkdir(parents=True, exist_ok=True)
    try:
        f = home / "brief.md"
        f.write_text("Draft the full set of legal documents: terms and conditions, privacy policy, contracts. " * 3)
        text = f"I've uploaded 1 file(s): {f}"
        got = cc._attached_text(text)
        assert "privacy policy" in got
        d = router.classify((text + "\n\n" + got), {})
        assert d.route == "astra" and router.classify(text, {}).route == "claude"
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_router_deploy_and_legal_signals():
    legal = ("deploy a team of legal specialists to analyse and provision anything that needs legal sign-off, "
             "t&c, privacy policy, contracts")
    assert router.classify(legal, {}).route == "astra"
    assert router.classify("deploy it to vercel", {}).route == "claude"
    assert router.classify("update the privacy policy date", {}).route == "claude"


def test_reply_that_leaves_background_work_running_is_finished(monkeypatch):
    d = _setup(monkeypatch)
    calls = []

    def run(prompt, **k):
        calls.append((prompt, k))
        if len(calls) == 1:
            k["on_event"]({"type": "system", "subtype": "task_started", "tool_use_id": "a1", "task_id": "t1",
                           "is_backgrounded": True, "task_type": "local_agent", "description": "Legal review"})
            return {"ok": True, "session_id": "S1", "billing": "s",
                    "result": "Both specialists are working. I'll check their work once they report back."}
        return {"ok": True, "session_id": "S1", "billing": "s", "result": "Done: terms and privacy policy drafted."}
    monkeypatch.setattr(cc, "run_claude", run)
    out = cc._execute(_req("chatL", "do the legal docs"))
    assert len(calls) == 2 and calls[1][1]["resume_session"] == "S1" and calls[1][0] == cc._UNFINISHED_NUDGE
    assert "terms and privacy policy drafted" in json.dumps(out)


def test_finished_reply_is_not_resumed(monkeypatch):
    _setup(monkeypatch)
    calls = []
    monkeypatch.setattr(cc, "run_claude", lambda prompt, **k: calls.append(k) or {
        "ok": True, "session_id": "S1", "billing": "s", "result": "Done: it printed hi. I'll check back if you need more."})
    cc._execute(_req("chatM", "run it"))
    assert len(calls) == 1  # no background work started: nothing to finish


def test_project_workspace_label_renames_only(tmp_path, monkeypatch):
    sys.path.insert(0, str(PKG.parents[1]))
    hp = import_module("hmem.projects")
    monkeypatch.setenv("HERMES_WEBUI_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("HERMES_WORKSPACES_DIR", str(tmp_path / "ws"))
    f = tmp_path / "workspaces.json"
    f.write_text(json.dumps([{"path": "/x/other", "name": "other"}, {"path": str(tmp_path / "ws" / "gs-56e2"), "name": "gs-56e2"}]))
    assert hp.label_webui_workspace({"id": "gs-56e2", "name": "GaffSwipe", "status": "active"})
    assert [w["name"] for w in json.loads(f.read_text())] == ["other", "GaffSwipe"]
    assert not hp.label_webui_workspace({"id": "new-1", "name": "New", "status": "active"})  # never adds
    assert hp.label_webui_workspace({"id": "gs-56e2", "name": "GaffSwipe", "status": "archived"})
    assert [w["name"] for w in json.loads(f.read_text())] == ["other"]
