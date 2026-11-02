"""Worker argv: lean vs full profile, MCP only when hand-offs are allowed, Codex mcp_servers overrides."""
from __future__ import annotations
import json
import re
from pathlib import Path

import pytest

from cc_helpers import prov, wire, wp

USER = {
    "model": "opus", "effortLevel": "high", "modelSettings": {"claude-opus-5-5": {"effortLevel": "medium"}},
    "autoMode": {"environment": ["x"]},
    "permissions": {"allow": ["Bash(npm run *)", "Bash(curl -H 'Authorization: Bearer sb_secret_abc')",
                              "Bash(curl -H 'apikey: xyz')", "Read(//tmp/**)"],
                    "additionalDirectories": ["/a"]},
    "enabledPlugins": {"frontend-design@claude-plugins-official": True, "impeccable@impeccable": True,
                       "vercel@claude-plugins-official": True, "caveman@caveman": True, "figma@x": True,
                       "stop-slop@hardikpandya-stop-slop": True, "ui-ux-pro-max@ui-ux-pro-max-skill": False},
    "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "node caveman-activate.js"}]}],
              "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "rtk hook claude"}]}]},
}


@pytest.fixture()
def home(tmp_path, monkeypatch):
    wire(tmp_path, monkeypatch)
    cdir = tmp_path / "claude"
    (cdir / "skills" / "motion").mkdir(parents=True)
    (cdir / "settings.json").write_text(json.dumps(USER), encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cdir))
    return tmp_path


def test_settings_keep_design_plugins_drop_secrets_and_caveman():
    s = wp.settings_for(USER)
    assert set(s["enabledPlugins"]) == {"frontend-design@claude-plugins-official", "impeccable@impeccable",
                                        "stop-slop@hardikpandya-stop-slop"}  # ui-ux-pro-max was off for the user
    allow = s["permissions"]["allow"]
    assert allow == ["Bash(npm run *)", "Read(//tmp/**)"] and "Bearer" not in json.dumps(s) and "apikey" not in json.dumps(s)
    assert s["permissions"]["additionalDirectories"] == ["/a"] and s["autoMode"] == USER["autoMode"]
    assert s["modelSettings"] == USER["modelSettings"] and s["effortLevel"] == "high"
    hooks = s["hooks"]
    assert "SessionStart" not in hooks and "UserPromptSubmit" not in hooks  # no caveman, no vercel hooks
    pre = hooks["PreToolUse"]
    assert pre[0]["matcher"] == "Bash" and pre[0]["hooks"][0]["command"] == "rtk hook claude"
    assert pre[1]["matcher"] == "Agent|Task" and re.search(r'limit-gate"? claude --purpose subagent', pre[1]["hooks"][0]["command"])


def test_lean_vs_full_argv(home):
    full = prov.build_claude_cmd({"worker_profile": "full"}, add_dirs=["/x"], resume_session=None, stream=True)
    assert "--setting-sources" not in full and "--strict-mcp-config" not in full and "Workflow" not in full
    lean = prov.build_claude_cmd({"worker_profile": "lean"}, add_dirs=["/x"], resume_session="sid", stream=True,
                                 model="claude-opus-5-5")
    assert lean[lean.index("--setting-sources") + 1] == "project,local"
    assert "--strict-mcp-config" in lean and "--exclude-dynamic-system-prompt-sections" in lean
    assert json.loads(Path(lean[lean.index("--mcp-config") + 1]).read_text(encoding="utf-8")) == {"mcpServers": {}}
    settings = json.loads(Path(lean[lean.index("--settings") + 1]).read_text(encoding="utf-8"))
    assert "vercel@claude-plugins-official" not in settings["enabledPlugins"]
    plug = Path(lean[lean.index("--plugin-dir") + 1])
    assert (plug / "skills").resolve() == (home / "claude" / "skills").resolve()  # all the user's own skills
    assert json.loads((plug / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["name"] == "user-skills"
    for t in ("Workflow", "ReportFindings", "ListAgents"):
        assert t in lean
    assert "--resume" in lean and lean[lean.index("--model") + 1] == "claude-opus-5-5"
    # rules: the static no-hand-off variant unless dynamic
    rules = lean[lean.index("--append-system-prompt") + 1]
    assert "DYNAMIC AGENTS" not in rules
    # default with no key = today's full behaviour
    assert "--setting-sources" not in prov.build_claude_cmd({}, add_dirs=[], resume_session=None, stream=False)


def test_keyword_switches_to_full_profile():
    assert wp.profile_for({"worker_profile": "lean"}, "deploy this to vercel") == "full"
    assert wp.profile_for({"worker_profile": "lean"}, "fix the parser") == "lean"
    assert wp.profile_for({"worker_profile": "full"}, "fix the parser") == "full"


def test_mcp_only_when_allowed(home, tmp_path):
    env = prov.delegate_env(request_dir=tmp_path / "r", parent_agent="claude-1", parent="claude", cwd="/p",
                            add_dirs=["/q"], model="claude-opus-5-5", effort="high", session_id="s")
    f = wp.mcp_config(tmp_path / "mcp.json", {"tabbie": prov.delegate_server_spec(env)})
    cmd = prov.build_claude_cmd({"worker_profile": "lean"}, add_dirs=[], resume_session=None, stream=True,
                                mcp_config=f, dynamic=True)
    srv = json.loads(Path(cmd[cmd.index("--mcp-config") + 1]).read_text(encoding="utf-8"))["mcpServers"]["tabbie"]
    assert srv["args"][0].endswith("delegate_mcp.py") and srv["env"]["TABBIE_PARENT_AGENT"] == "claude-1"
    assert srv["env"]["TABBIE_DELEGATE_FROM"] == "claude" and json.loads(srv["env"]["TABBIE_ADD_DIRS"]) == ["/q"]
    rules = cmd[cmd.index("--append-system-prompt") + 1]
    assert "DYNAMIC AGENTS" in rules and "Codex (OpenAI GPT)" in rules and "delegate" in rules
    assert "ANTHROPIC_API_KEY" not in json.dumps(srv)


def test_codex_mcp_overrides_and_delegate_cmd(tmp_path):
    env = prov.delegate_env(request_dir=tmp_path, parent_agent="astra-1", parent="codex", cwd="/p", add_dirs=[],
                            model="gpt-6-astra", effort=None)
    over = prov.codex_mcp_overrides(env)
    joined = " ".join(over)
    assert "mcp_servers.tabbie.command=" in joined and "delegate_mcp.py" in joined
    assert "mcp_servers.tabbie.tool_timeout_sec=10800" in joined and 'TABBIE_DELEGATE_FROM = "codex"' in joined
    cmd = prov.build_codex_exec_cmd(model="gpt-6-astra", effort=None, out_file=tmp_path / "o", cwd="/p", add_dirs=[],
                                    extra=over)
    assert cmd[-1] == "-" and over[1] in cmd and "-s" in cmd and cmd[cmd.index("-s") + 1] == "workspace-write"
    ro = prov.build_codex_exec_cmd(model="gpt-6-astra", effort=None, out_file=tmp_path / "o", cwd="/p", add_dirs=[],
                                   sandbox="read-only", ephemeral=True)
    assert ro[ro.index("-s") + 1] == "read-only" and "--ephemeral" in ro and not any("tabbie" in x for x in ro)


def test_depth_one_has_no_delegate_tool(monkeypatch):
    monkeypatch.setenv("TABBIE_LINKED", "claude,codex")
    monkeypatch.delenv("TABBIE_DELEGATE_DEPTH", raising=False)
    assert prov.dynamic_gate()[0] is True
    monkeypatch.setenv("TABBIE_DELEGATE_DEPTH", "1")
    ok, why = prov.dynamic_gate()
    assert not ok and "delegate" in why


def test_worker_rules_variants_are_static():
    a = prov.worker_rules("claude", dynamic=True)
    assert a == prov.worker_rules("claude", dynamic=True)  # byte-identical: the prefix caches
    b = prov.worker_rules("codex", dynamic=True)
    assert "Claude Code (Anthropic Claude)" in b and re.search(r'limit-gate"? codex', b) and "spawn_agent" in b
    assert prov.worker_rules("claude", step_commits=False, uat=False) == prov.WORKER_RULES.format(max_subagents=2)
    both = prov.worker_rules("claude", step_commits=True, uat=True)
    assert both.startswith(prov.WORKER_RULES.format(max_subagents=2))
    assert "unless this request says otherwise" in both and "never as passed" in both
    assert "Hint: GPT for SQL" in prov.worker_rules("claude", dynamic=True, hints=["GPT for SQL"])
