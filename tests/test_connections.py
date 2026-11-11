"""Settings → Providers data: subscription plans and services found from config names, never secrets."""
from __future__ import annotations
import base64, importlib.util, json, sys
from importlib import import_module
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "plugin" / "command-centre"
if "command_centre" not in sys.modules:
    spec = importlib.util.spec_from_file_location("command_centre", PKG / "__init__.py",
                                                  submodule_search_locations=[str(PKG)])
    _m = importlib.util.module_from_spec(spec); sys.modules["command_centre"] = _m
    spec.loader.exec_module(_m)
cn = import_module("command_centre.connections")


def _jwt(claims: dict) -> str:
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")  # noqa: E731
    return f"{enc({'alg': 'none'})}.{enc(claims)}.sig"


def test_claude_status_plan():
    ok = json.dumps({"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty", "subscriptionType": "max"})
    assert cn.parse_claude_status(ok) == (True, "Claude Max", "")
    key = json.dumps({"loggedIn": True, "authMethod": "api_key", "apiProvider": "firstParty"})
    assert cn.parse_claude_status(key)[0] is False
    assert cn.parse_claude_status(json.dumps({"loggedIn": False}))[:2] == (False, "")
    assert cn.parse_claude_status("not json")[0] is None


def test_codex_plan_from_id_token(tmp_path):
    f = tmp_path / "auth.json"
    f.write_text(json.dumps({"tokens": {"id_token": _jwt({"https://api.openai.com/auth": {"chatgpt_plan_type": "plus"}}),
                                        "access_token": "secret-access"}}))
    assert cn.codex_plan(f) == "ChatGPT Plus"
    f.write_text("{}")
    assert cn.codex_plan(f) == ""
    assert cn.codex_plan(tmp_path / "missing.json") == ""


def test_workers_respect_forced_link(monkeypatch):
    monkeypatch.setenv("TABBIE_LINKED", "claude")
    cn._cache.clear()
    ws = {w["id"]: w for w in cn.workers(refresh=True)}
    assert ws["claude"]["signed_in"] is True and ws["codex"]["signed_in"] is False
    assert ws["claude"]["manage_url"].startswith("https://claude.ai")
    cn._cache.clear()


def test_services_from_config_names_only(tmp_path):
    home, hermes = tmp_path / "home", tmp_path / "hermes"
    (home / ".claude").mkdir(parents=True)
    (home / ".codex").mkdir()
    hermes.mkdir()
    (home / ".claude.json").write_text(json.dumps({"mcpServers": {
        "higgsfield": {"type": "http", "url": "https://mcp.higgsfield.ai/mcp", "headers": {"Authorization": "Bearer SECRET"}},
        "my_tool": {"command": "x"}}}))
    (home / ".claude" / "settings.json").write_text(json.dumps({"enabledPlugins": {
        "figma@claude-plugins-official": True, "caveman@caveman": True, "vercel@x": False}}))
    (home / ".codex" / "config.toml").write_text('[mcp_servers.elevenlabs]\ncommand = "uvx"\nenv = { ELEVENLABS_API_KEY = "SECRET" }\n')
    (hermes / "config.yaml").write_text("mcp_servers:\n  elevenlabs:\n    url: https://x\n")
    import os
    old = os.environ.pop("CODEX_HOME", None)
    try:
        got = cn.services(home=home, hermes=hermes, which=lambda c: "/bin/" + c if c == "higgsfield" else None)
    finally:
        if old is not None:
            os.environ["CODEX_HOME"] = old
    by = {s["id"]: s for s in got}
    assert by["higgsfield"]["where"] == ["Claude Code", "Command line"]
    assert by["elevenlabs"]["where"] == ["Codex", "Hub"]
    assert by["figma"]["name"] == "Figma"
    assert "caveman" not in by and "vercel" not in by  # skill packs and disabled plugins aren't services
    assert by["my_tool"]["name"] == "My Tool"
    assert [s["id"] for s in got][-1] == "my_tool"  # unknown ones after the known services
    assert "SECRET" not in json.dumps(got)


hs = import_module("command_centre.hub_setup")


def test_hub_model_validation():
    assert hs.model_ok("anthropic", "claude-opus-5-5") and hs.model_ok("anthropic", "claude-fable-5-2")
    assert not hs.model_ok("anthropic", "gpt-6.1-sol")
    assert hs.model_ok("openai-codex", "gpt-6-astra") and not hs.model_ok("openai-codex", "claude-haiku-5-5")
    assert not hs.model_ok("anthropic", "claude-unknown-1-0")


def test_hub_use_saves_chosen_model(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(hs, "_hermes", lambda *a, **k: (calls.append(a), (0, ""))[1])
    monkeypatch.setattr(hs, "ROOT", tmp_path)
    r = hs.use("anthropic", "claude-opus-5-5")
    assert r == {"ok": True, "provider": "anthropic", "model": "claude-opus-5-5"}
    assert ("config", "set", "model.provider", "anthropic") in calls
    assert ("config", "set", "model.default", "claude-opus-5-5") in calls
    import pytest
    with pytest.raises(ValueError):
        hs.use("anthropic", "gpt-6.1-sol")
    with pytest.raises(ValueError):
        hs.use("openai", None)


def test_hub_options_lists_both_logins(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    (home / "config.yaml").write_text("model:\n  provider: openai-codex\n  default: gpt-6.1-sol\n")
    (home / "auth.json").write_text(json.dumps({"credential_pool": {"anthropic": [{"source": "claude_code_linked"}]}}))
    monkeypatch.setattr(hs, "hermes_home", lambda: home)
    o = hs.options()
    assert o["current"] == {"provider": "openai-codex", "model": "gpt-6.1-sol"}
    assert o["claude_linked"] is True
    assert [m["family"] for m in o["models"]["anthropic"]] == ["opus", "fable", "sonnet", "haiku"]
    assert all(m["id"].startswith("claude-") for m in o["models"]["anthropic"])
    assert o["models"]["openai-codex"][0]["family"] == "sol"
