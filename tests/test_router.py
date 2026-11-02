"""Offline tests for the command-centre router, reply parsing and attachment pipeline.
Run: ~/.hermes/hermes-agent/venv-python -m pytest ~/hermes/tests  (or any python3 with pyyaml)."""
from __future__ import annotations
import importlib.util, sys, tempfile, zipfile, base64
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "plugin" / "command-centre"
spec = importlib.util.spec_from_file_location("command_centre", PKG / "__init__.py",
                                              submodule_search_locations=[str(PKG)])
cc = importlib.util.module_from_spec(spec); sys.modules["command_centre"] = cc
from importlib import import_module
spec.loader.exec_module(cc)
router = import_module("command_centre.router")
att = import_module("command_centre.attachments")
CFG = {"router": {"plan_threshold": 3}}

DIRECT = ["change the login button to blue", "make this padding 16px", "fix this TypeScript error",
          "rename this component", "move this text down", "remove this animation", "change this copy",
          "make this modal wider", "fix the broken mobile navbar", "run the tests", "commit this",
          "Create a test text file containing hello.", "add a loading spinner to the submit button",
          "why is the build failing on CI?", "update the README with the new install steps"]
PLAN = ["redesign an entire onboarding flow", "Design and build a new onboarding experience.",
        "redesign my website", "create a marketing campaign", "create a launch strategy",
        "create a new application", "overhaul the visual identity", "build a complex integration with Stripe and HubSpot",
        "migrate the backend from Express to Fastify", "make me a 30 second advert for my app",
        "rethink the entire landlord onboarding experience"]

def test_direct():
    for t in DIRECT:
        d = router.classify(t, CFG); assert d.route == "claude", (t, d)

def test_plan():
    for t in PLAN:
        d = router.classify(t, CFG); assert d.route == "astra", (t, d)

def test_overrides():
    d = router.classify("@claude redesign the whole app", CFG)
    assert d.route == "claude" and d.forced and d.text == "redesign the whole app"
    d = router.classify("@astra make the button black", CFG)
    assert d.route == "astra" and d.forced
    assert router.classify("@claudeX hi", CFG).forced is False

def test_replies():
    assert router.interpret_reply("yes")[0] == "approve"
    assert router.interpret_reply("Looks good!")[0] == "approve"
    assert router.interpret_reply("go")[0] == "approve"
    k, m = router.interpret_reply("yes but remove the video hero")
    assert k == "approve_with_changes" and m == "remove the video hero"
    assert router.interpret_reply("cancel")[0] == "cancel"
    assert router.interpret_reply("use a darker palette instead")[0] == "revise"
    assert router.interpret_reply("good question, it's for landlords")[0] == "revise"

def test_attachments():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        png = tmp / "shot.png"; png.write_bytes(base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="))
        z = tmp / "bundle.zip"
        with zipfile.ZipFile(z, "w") as zf: zf.writestr("a/readme.md", "hi")
        msg = [{"type": "text", "text": f'see @file:"{png}" and {z} please'},
               {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png.read_bytes()).decode()}}]
        m = att.Manifest(tmp / "req")
        added = m.ingest(msg, {})
        kinds = sorted(e["kind"] for e in added)
        assert kinds == ["archive", "image"], kinds  # inline copy of same png deduped by sha256
        for e in added:
            stored = Path(e["stored_path"])
            assert stored.read_bytes() == Path(e["original_path"]).read_bytes()
            assert len(e["sha256"]) == 64 and e["size_bytes"] > 0
        zipe = next(e for e in added if e["kind"] == "archive")
        assert Path(zipe["derived"][0]["path"], "a/readme.md").read_text(encoding="utf-8") == "hi"
        assert "original:" in m.render()

def test_specific_beats_keyword():
    assert router.classify("fix the typo on the campaign page", CFG).route == "claude"
    assert router.classify("change the strategy section heading to 24px", CFG).route == "claude"

def test_project_inference_ignores_paths(monkeypatch=None):
    projects = import_module("command_centre.projects")
    projects.known_projects = lambda: {"hermes": "/tmp", "space-game": "/var"}
    projects.current = lambda: None
    projects.write_json = lambda *a, **k: None
    p, src = projects.resolve("look at @file:/Users/x/hermes/a.png and /Users/x/hermes/b.md", None)
    assert src == "unresolved", (p, src)
    p, src = projects.resolve("fix the jump bug in space-game", None)
    assert p == "/var" and src == "inferred from message"

def test_generic_chat_detection():
    for t in ["what's the difference between SSE and websockets?", "hi", "explain OAuth PKCE",
              "should I use Postgres or SQLite for this?"]:
        assert router.looks_like_chat(t), t
    for t in ["fix the navbar", "can you fix the navbar", "create a test file containing hello",
              "make a 10s voiceover saying welcome"]:
        assert not router.looks_like_chat(t), t

def test_workspace_selection():
    projects = import_module("command_centre.projects")
    projects.known_projects = lambda: {"acme": "/tmp/gs"}
    projects.current = lambda: "/tmp/other"
    import os; os.makedirs("/tmp/gs/src", exist_ok=True)
    assert projects.resolve("fix the button", str(projects.GENERAL_DIR)) == (None, "general")
    assert projects.resolve("fix the button", "/tmp/gs/src") == ("/tmp/gs", "session workspace")

def test_multi_folder_workspace(tmp_path=None):
    import tempfile, os
    projects = import_module("command_centre.projects")
    common = import_module("command_centre.common")
    d = Path(tempfile.mkdtemp())
    a, b = d / "app", d / "api"; a.mkdir(); b.mkdir()
    store = {}
    projects.load_projects_file = lambda: store
    projects.save_projects_file = lambda data: store.update(data)
    projects.WORKSPACES_DIR = d / "workspaces"
    projects._add_webui_workspace = lambda *a: None
    projects._mirror_hermes_project = lambda *a: None
    projects._discover = lambda: {}
    ok, msg = projects.create_workspace("Acme Shop", [str(a), str(b)])
    assert ok, msg
    wdir = d / "workspaces" / "acme-shop"
    assert (wdir / "app").resolve() == a.resolve() and (wdir / "api").resolve() == b.resolve()
    path, src = projects.resolve("fix the signup bug", str(wdir))
    assert src == "session workspace" and path == str(wdir)
    primary, extras = projects.folders_for(path)
    assert primary == str(a.resolve()) and extras == [str(b.resolve())]

def test_takeover_flow(monkeypatch=None):
    import tempfile, json as _j
    common = import_module("command_centre.common")
    st = import_module("command_centre.state")
    prov = import_module("command_centre.providers")
    d = Path(tempfile.mkdtemp())
    for mod in (st,):
        mod.REQUESTS_DIR, mod.SESSIONS_DIR = d / "req", d / "ses"
    cc.REQUESTS_DIR, cc.LOCKS_DIR = d / "req", d / "locks"
    ag = import_module("command_centre.agents")
    ag.REQUESTS_DIR, ag.AGENTS_INDEX_DIR = d / "req", d / "agents"
    cc.log_decision = lambda **k: None
    cc.load_config = lambda: {"executor": {}}
    cc._CLAUDE_SESSIONS = d / "cs.json"
    cc.run_claude = lambda *a, **k: {"ok": False, "error": "Claude AI usage limit reached|resets 3pm", "billing": "sub"}
    calls = []
    cc.run_astra_executor = lambda prompt, **k: calls.append(prompt) or {"ok": True, "result": "Done.", "model": "gpt-6-astra", "billing": "chatgpt"}
    proj = d / "proj"; proj.mkdir()
    r = st.Request.create(session_id="s1", route="claude", reason="x", forced=False, prompt="fix it",
                          raw_message="fix it", project=str(proj), project_source="t")
    cc.agents = lambda: type("A", (), {"has": staticmethod(lambda a: True)})()
    linked = prov.linked
    cc.providers.linked = lambda a: True
    cc._codex_models_safe = lambda: []
    try:
        # Claude hits its limit mid-task: Codex carries on with the same task, no question asked
        out = cc._execute(r)
        assert out["ok"] and "took over" in out["route"] and calls and "fix it" in calls[-1]
        assert not st.session_state("s1").get("takeover_offer")
        # both out: one line naming both limits, nothing hands over
        cc.run_astra_executor = lambda prompt, **k: {"ok": False, "error": "You've hit your usage limit. Try again at 6pm.", "model": "gpt-6-astra"}
        r2 = st.Request.create(session_id="s1", route="claude", reason="x", forced=False, prompt="again",
                               raw_message="again", project=str(proj), project_source="t")
        out2 = cc._execute(r2)
        assert not out2["ok"] and "Claude hit its usage limit (resets 3pm)" in out2["usage_limit"]
        assert "Codex hit its usage limit (resets 6pm)" in out2["usage_limit"] and "Both are at their limits" in out2["usage_limit"]
        cc.run_astra_executor = lambda prompt, **k: calls.append(prompt) or {"ok": True, "result": "Done.", "model": "gpt-6-astra", "billing": "chatgpt"}
        # /takeover enables and later disables
        assert "Takeover on" in cc._cmd_takeover("", session_id="s1")
        r3 = st.Request.create(session_id="s1", route="claude", reason="x", forced=False, prompt="third",
                               raw_message="third", project=str(proj), project_source="t")
        out3 = cc._execute(r3)
        assert out3["ok"] and "takeover" in out3["route"] and calls and "fix it" not in calls[-1] and "third" in calls[-1]
        assert "Takeover off" in cc._cmd_takeover("", session_id="s1")
    finally:
        cc.providers.linked = linked
    assert prov.usage_limit_info({"ok": False, "error": "5-hour limit reached ∙ resets 9pm"})
    assert prov.usage_limit_info({"ok": False, "error": "SyntaxError"}) is None

def test_webui_project_spaces():
    import tempfile, json as _j
    projects = import_module("command_centre.projects")
    d = Path(tempfile.mkdtemp()); (d / "sessions").mkdir()
    a, b = d / "app", d / "site"; a.mkdir(); b.mkdir()
    projects.WEBUI_STATE = d
    projects.folders_for = lambda p: (p, [])
    (d / "sessions" / "s9.json").write_text(_j.dumps({"project_id": "p1"}), encoding="utf-8")
    (d / "projects.json").write_text(_j.dumps([{"project_id": "p1", "workspaces": [str(a), str(b)], "default_workspace": str(a)}]), encoding="utf-8")
    assert projects.expand_with_project("s9", str(b), []) == (str(b), [str(a)])
    assert projects.expand_with_project("s9", None, []) == (str(a), [str(b)])
    assert projects.expand_with_project("nope", "/x", []) == ("/x", [])


def test_hub_tool_requests():
    board = ["add buy milk to my todos", "move the login card to done", "put fix invoices on the kanban",
             "what is on my todo list", "show my board", "add todo: call mum"]
    schedule = ["remind me every monday at 9 to send the report", "schedule a daily check of my inbox"]
    work = ["fix the navbar on the board game app", "add a todo list feature to my app", "run the tests",
            "make the cards on the landing page rounder", "every time I click save it crashes, fix it",
            "add a kanban board page to the dashboard app"]
    for t in board:
        assert router.hub_tool_request(t) == "board", t
    for t in schedule:
        assert router.hub_tool_request(t) == "schedule", t
    for t in work:
        assert router.hub_tool_request(t) is None, t


def test_codex_limit_mid_task_hands_the_same_task_to_claude():
    import tempfile
    st = import_module("command_centre.state")
    prov = import_module("command_centre.providers")
    ag = import_module("command_centre.agents")
    d = Path(tempfile.mkdtemp())
    st.REQUESTS_DIR, st.SESSIONS_DIR = d / "req", d / "ses"
    cc.REQUESTS_DIR, cc.LOCKS_DIR = d / "req", d / "locks"
    ag.REQUESTS_DIR, ag.AGENTS_INDEX_DIR = d / "req", d / "agents"
    cc.log_decision = lambda **k: None
    cc.load_config = lambda: {"executor": {}}
    cc._CLAUDE_SESSIONS = d / "cs.json"
    cc.agents = lambda: type("A", (), {"has": staticmethod(lambda a: True)})()
    cc._codex_models_safe = lambda: []
    linked, prov.linked = prov.linked, (lambda a: True)
    cc.run_astra_executor = lambda prompt, **k: {"ok": False, "error": "You've hit your usage limit. Try again at 6pm.", "model": "gpt-6-astra"}
    got = []
    cc.run_claude = lambda prompt, **k: got.append((prompt, k.get("model"))) or {"ok": True, "result": "Done.", "billing": "sub"}
    proj = d / "proj"; proj.mkdir()
    r = st.Request.create(session_id="s9", route="claude", reason="x", forced=False, prompt="build the page",
                          raw_message="build the page", project=str(proj), project_source="t")
    try:
        out = cc._astra_takeover(r, reason="picked", choice={"provider": "openai", "model": "gpt-6-astra", "name": "GPT-6 Astra", "effort": "medium"})
    finally:
        prov.linked = linked
    assert out["ok"] and got and "build the page" in got[0][0] and str(got[0][1]).startswith("claude-")


def test_takeover_off_stops_the_handover(tmp_path, monkeypatch):
    from hmem import agents as agents_mod
    monkeypatch.setattr(cc, "agents", lambda: agents_mod)
    monkeypatch.setattr(agents_mod, "path", lambda: tmp_path / "agents.yaml")
    assert agents_mod.takeover_enabled() and cc._handover_on()  # no file: on by default
    agents_mod.save(["claude", "codex"], takeover=False)
    assert "takeover: false" in (tmp_path / "agents.yaml").read_text(encoding="utf-8")
    assert not agents_mod.takeover_enabled() and not cc._handover_on()
    agents_mod.save()  # other changes keep it off
    assert not agents_mod.takeover_enabled()
