"""Per-conversation executor choice (composer model picker): storage, command building, dispatch."""
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
ec = import_module("command_centre.executor_choice")
st = import_module("command_centre.state")
prov = import_module("command_centre.providers")


def _tmp_state():
    d = Path(tempfile.mkdtemp())
    st.REQUESTS_DIR, st.SESSIONS_DIR = d / "req", d / "ses"
    return d


def test_default_is_most_capable_opus():
    _tmp_state()
    ch = ec.get_choice("fresh")
    assert (ch["provider"], ch["model"], ch["effort"], ch["name"]) == ("anthropic", "claude-opus-5-5", None, "Opus 5.5")
    assert ch["default"] is True


def test_set_and_get_per_session():
    _tmp_state()
    ec.set_choice("a", "openai", "gpt-6.1-sol", "low")
    ec.set_choice("b", "anthropic", "claude-sonnet-5-5")
    a, b = ec.get_choice("a"), ec.get_choice("b")
    assert (a["provider"], a["model"], a["effort"], a["worker"]) == ("openai", "gpt-6.1-sol", "low", "Codex")
    assert (b["model"], b["effort"], b["default"]) == ("claude-sonnet-5-5", None, False)
    assert ec.get_choice("c")["model"] == "claude-opus-5-5"
    # stored next to the rest of the session state, other keys untouched
    s = st.session_state("a"); s["takeover"] = False; st.save_session_state("a", s)
    ec.set_choice("a", "openai", "gpt-6-astra")
    assert st.session_state("a")["takeover"] is False and ec.get_choice("a")["model"] == "gpt-6-astra"


def test_rejects_unknown_and_heals_bad_state():
    _tmp_state()
    for args in (("openai", "claude-opus-5-5"), ("anthropic", "opus"), ("x", "y"), ("openai", "gpt-6-luna", "ultra")):
        try:
            ec.set_choice("s", *args)
            assert False, args
        except ValueError:
            pass
    st.save_session_state("s", {"executor_choice": {"provider": "openai", "model": "gone"}})
    assert ec.get_choice("s")["model"] == "claude-opus-5-5"


def test_catalog_filters_openai_by_login():
    cat = ec.public_catalog(["gpt-6-astra", "gpt-6.1-sol"])
    assert [m["id"] for m in cat["providers"]["openai"]["models"]] == ["gpt-6-astra", "gpt-6.1-sol"]
    assert [m["name"] for m in cat["providers"]["anthropic"]["models"]][0] == "Opus 5.5"
    assert cat["efforts"] == ["low", "medium", "high"]


def test_claude_cmd_building():
    cmd = prov.build_claude_cmd({"model": "sonnet"}, add_dirs=["/x"], resume_session="sid", stream=True,
                                model="claude-opus-5-5", effort="high")
    i = cmd.index("--model"); assert cmd[i + 1] == "claude-opus-5-5"
    assert cmd[cmd.index("--effort") + 1] == "high" and "--resume" in cmd and "stream-json" in cmd
    base = prov.build_claude_cmd({}, add_dirs=[], resume_session=None, stream=False)
    assert "--model" not in base and "--effort" not in base and "json" in base
    cfg_only = prov.build_claude_cmd({"model": "sonnet"}, add_dirs=[], resume_session=None, stream=False)
    assert cfg_only[cfg_only.index("--model") + 1] == "sonnet"


def test_codex_cmd_building(tmp_path):
    out = tmp_path / "o.txt"
    cmd = prov.build_codex_exec_cmd(model="gpt-6.1-sol", effort="medium", out_file=out, cwd="/p", add_dirs=["/q"])
    assert cmd[:2] == ["codex", "exec"] and cmd[cmd.index("-m") + 1] == "gpt-6.1-sol"
    assert 'model_reasoning_effort="medium"' in cmd and cmd[-1] == "-" and "--add-dir" in cmd
    no_eff = prov.build_codex_exec_cmd(model="gpt-6-luna", effort=None, out_file=out, cwd="/p", add_dirs=[])
    assert not any("model_reasoning_effort" in x for x in no_eff)
    res = prov.build_codex_exec_cmd(model="gpt-6-astra", effort=None, out_file=out, cwd="/p", add_dirs=["/q"],
                                    resume_thread="t1")
    assert res[:4] == ["codex", "exec", "resume", "t1"] and "-C" not in res


def _wire(d):
    cc.REQUESTS_DIR, cc.LOCKS_DIR = d / "req", d / "locks"
    ag = import_module("command_centre.agents")
    ag.REQUESTS_DIR, ag.AGENTS_INDEX_DIR = d / "req", d / "agents"
    cc.log_decision = lambda **k: None
    cc.load_config = lambda: {"executor": {}}
    cc._CLAUDE_SESSIONS = d / "cs.json"


def test_execute_dispatches_by_choice():
    d = _tmp_state(); _wire(d)
    claude_calls, codex_calls = [], []
    cc.run_claude = lambda prompt, **k: claude_calls.append(k) or {"ok": True, "result": "Done.", "billing": "sub"}
    cc.run_astra_executor = lambda prompt, **k: codex_calls.append((prompt, k)) or {
        "ok": True, "result": "Done.", "model": k.get("model"), "billing": "chatgpt", "thread_id": "th"}
    proj = d / "proj"; proj.mkdir()

    def req(text):
        return st.Request.create(session_id="s9", route="claude", reason="scoped", forced=False, prompt=text,
                                 raw_message=text, project=str(proj), project_source="t")

    out = cc._execute(req("one"))  # default: Claude Code with Opus 5.5
    assert out["ok"] and claude_calls[-1]["model"] == "claude-opus-5-5" and claude_calls[-1]["effort"] is None
    assert out["route"].startswith("Route: Claude Opus 5.5 —")

    ec.set_choice("s9", "openai", "gpt-6.1-sol", "low")
    r = req("two")
    out = cc._execute(r)
    prompt, kw = codex_calls[-1]
    assert (kw["model"], kw["effort"]) == ("gpt-6.1-sol", "low") and "two" in prompt
    assert "taking over" not in prompt and len(claude_calls) == 1
    saved = st.Request(r.id).data["executor"]
    assert saved["provider"] == "codex-cli" and saved["model"] == "gpt-6.1-sol" and saved["chosen"]
    assert out["route"].startswith("Route: Codex GPT-6.1 Sol")
    agents = json.loads((d / "req" / r.id / "agents.json").read_text())
    assert agents["agents"][0]["model"] == "gpt-6.1-sol"

    ec.set_choice("s9", "anthropic", "claude-opus-5-5", "high")
    cc._execute(req("three"))
    assert claude_calls[-1]["model"] == "claude-opus-5-5" and claude_calls[-1]["effort"] == "high"


def test_hub_is_told_about_messages_added_mid_run(monkeypatch):
    d = _tmp_state(); _wire(d)
    monkeypatch.setattr(cc, "_learn", lambda r: None)  # its background thread would race later memory tests
    cc.run_claude = lambda prompt, **k: {"ok": True, "result": "13\n\nAlso, 36 .js files.", "billing": "sub",
                                         "live_messages": ["Also tell me how many .js files there are."]}
    proj = d / "proj"; proj.mkdir()
    r = st.Request.create(session_id="s10", route="claude", reason="scoped", forced=False,
                          prompt="count the .py files, reply with just the number", raw_message="x",
                          project=str(proj), project_source="t")
    out = cc._execute(r)
    assert out["user_messages_during_run"] == ["Also tell me how many .js files there are."]
    assert '"Also tell me how many .js files there are."' in out["note"]
    assert "original request" in out["note"] and "priority" in out["note"]
    assert st.Request(r.id).data["result"]["live_messages"]  # kept on the request too

    cc.run_claude = lambda prompt, **k: {"ok": True, "result": "13", "billing": "sub", "live_messages": []}
    out = cc._execute(st.Request.create(session_id="s11", route="claude", reason="scoped", forced=False,
                                        prompt="p", raw_message="p", project=str(proj), project_source="t"))
    assert "note" not in out and "user_messages_during_run" not in out


def test_new_chat_choice_follows_enabled_agents(monkeypatch):
    _tmp_state()
    d = ec.new_chat_choice()
    assert (d["provider"], d["model"], d["name"], d["worker"], d["default"]) == (
        "anthropic", "claude-opus-5-5", "Opus 5.5", "Claude Code", True)
    monkeypatch.setattr(ec, "providers_on", lambda: ["openai"])  # Claude agent switched off
    d = ec.new_chat_choice()
    assert (d["provider"], d["model"], d["name"], d["worker"]) == ("openai", "gpt-6-astra", "GPT-6 Astra", "Codex")
    assert d == {**ec.get_choice("never-saved"), "default": True}


def _sidecar():
    spec = importlib.util.spec_from_file_location("cc_sidecar_x", PKG / "sidecar.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def _get(sc, path, monkeypatch):
    """Drive Handler.do_GET without a socket; returns (status, body)."""
    monkeypatch.setattr(sc, "_token_ok", lambda _t: True)
    h = sc.Handler.__new__(sc.Handler)
    h.path, h.headers, out = path, {}, {}
    h._send = lambda code, body: out.update(code=code, body=body)
    h.do_GET()
    return out["code"], out["body"]


def test_sidecar_executor_without_id_returns_new_chat_default(monkeypatch):
    sc = _sidecar()
    monkeypatch.setattr(sc.providers, "codex_models", lambda: ["gpt-6.1-sol"])
    monkeypatch.setattr(sc.executor_choice, "providers_on", lambda: ["openai"])
    for path in ("/executor", "/executor?id="):
        code, body = _get(sc, path, monkeypatch)
        assert code == 200 and body["session_id"] is None
        assert (body["choice"]["provider"], body["choice"]["default"]) == ("openai", True)
        assert list(body["catalog"]["providers"]) == ["openai"]
        assert [m["id"] for m in body["catalog"]["providers"]["openai"]["models"]] == ["gpt-6.1-sol"]
    code, _ = _get(sc, "/executor?id=bad%20id!", monkeypatch)
    assert code == 400
