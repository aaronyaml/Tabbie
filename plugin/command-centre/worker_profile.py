"""Lean Claude Code worker profile (router.yaml executor.worker_profile: lean | full).

A worker used to inherit the user's whole ~/.claude environment: every plugin, 130+ skills, three MCP
servers (one of which times out at 30s), the Vercel SessionStart hook and the caveman hooks, about
19k tokens before the task. The lean profile starts Claude Code with:

  --setting-sources project,local   the user's ~/.claude/settings.json is not read (its plugins and
                                    hooks stay out); the project's own settings still apply
  --settings state/worker/claude-settings.json
        generated: a whitelist of the user's settings (permissions without secret-looking rules,
        autoMode, model, effort and per-model settings), enabledPlugins = the design plugins kept by
        executor.plugins_keep, the user's `rtk` Bash hook, and a PreToolUse Agent|Task hook that runs
        bin/limit-gate before a subagent starts
  --plugin-dir state/worker/user-skills
        a tiny local plugin whose skills/ links to ~/.claude/skills: all the user's own skills stay
        (listed as user-skills:<name>), since dropping the user setting source would hide them
  --strict-mcp-config --mcp-config <file>
        no user / plugin / claude.ai MCP servers; only Tabbie's own `tabbie` delegate server when
        dynamic agents are on
  --exclude-dynamic-system-prompt-sections
        per-machine sections move out of the system prompt, so the cached prefix is shared across
        folders and chats
  --disallowedTools Workflow ReportFindings ListAgents (large schemas a worker never needs)

The user's own ~/.claude files (settings, CLAUDE.md, caveman flag files) are never written.
Verified on Claude Code 2.1.314 (spike V1): OAuth works, flag-settings hooks fire, the plugin
allowlist applies, --strict-mcp-config also drops claude.ai connectors, ~/.claude/CLAUDE.md still
loads, user skills load through the plugin dir.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .common import ROOT, STATE_DIR, write_json

WORKER_DIR = STATE_DIR / "worker"
SETTINGS_FILE = WORKER_DIR / "claude-settings.json"
SKILLS_PLUGIN = WORKER_DIR / "user-skills"
LIMIT_GATE = ROOT / "bin" / "limit-gate"
DEFAULT_KEEP = ["frontend-design", "impeccable", "ui-ux-pro-max", "taste-skill", "stop-slop"]
LEAN_DISALLOWED = ["Workflow", "ReportFindings", "ListAgents"]
# A request that names one of the dropped plugins' tools runs with the full profile instead.
FULL_PROFILE_RX = re.compile(r"\b(vercel|figma|obsidian|cleo|brag|caveman|rust[- ]analy[sz]er)\b", re.I)
_SECRETISH = re.compile(r"(?i)bearer|api[-_]?key|apikey|secret|token|password|passwd|sk-[a-z0-9]|eyJ[a-z0-9]")
_COPY_KEYS = ("model", "effortLevel", "modelSettings", "autoMode", "alwaysThinkingEnabled", "includeCoAuthoredBy")


def _user_settings() -> dict:
    claude_dir = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    try:
        return json.loads((claude_dir / "settings.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _clean_rules(rules) -> list[str]:
    """Permission rules minus anything that looks like it carries a credential (never copied to disk)."""
    return [r for r in (rules or []) if isinstance(r, str) and not _SECRETISH.search(r)]


def settings_for(user: dict, keep: list[str] | None = None) -> dict:
    """The worker's --settings JSON, built from the user's settings (pure; tested)."""
    keep = list(keep if keep is not None else DEFAULT_KEEP)
    out: dict = {k: user[k] for k in _COPY_KEYS if k in user}
    perms = user.get("permissions") or {}
    p: dict = {}
    for k in ("allow", "deny", "ask"):
        if perms.get(k):
            p[k] = _clean_rules(perms[k])
    for k in ("additionalDirectories", "defaultMode"):
        if perms.get(k):
            p[k] = perms[k]
    if p:
        out["permissions"] = p
    on = {k for k, v in (user.get("enabledPlugins") or {}).items() if v}
    out["enabledPlugins"] = {k: True for k in sorted(on) if k.split("@", 1)[0] in keep}
    hooks: dict = {}
    for entry in ((user.get("hooks") or {}).get("PreToolUse") or []):  # keep rtk's Bash rewriter only
        cmds = [h for h in entry.get("hooks") or [] if str(h.get("command", "")).strip().startswith("rtk ")]
        if cmds:
            hooks.setdefault("PreToolUse", []).append({**entry, "hooks": cmds})
    hooks.setdefault("PreToolUse", []).append({
        "matcher": "Agent|Task",
        "hooks": [{"type": "command", "command": f"{LIMIT_GATE} claude --purpose subagent", "timeout": 20}]})
    out["hooks"] = hooks
    return out


def _write_if_changed(path: Path, data) -> None:
    text = json.dumps(data, indent=2, sort_keys=True)
    try:
        if path.read_text(encoding="utf-8") == text:
            return
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def ensure(ecfg: dict) -> dict:
    """Write/refresh the lean profile files; returns {settings, plugin_dir (or None)}."""
    _write_if_changed(SETTINGS_FILE, settings_for(_user_settings(), ecfg.get("plugins_keep")))
    skills = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "skills"
    plugin_dir = None
    if skills.is_dir():
        man = SKILLS_PLUGIN / ".claude-plugin" / "plugin.json"
        _write_if_changed(man, {"name": "user-skills", "version": "1.0.0",
                                "description": "The user's own skills (~/.claude/skills) for Tabbie workers"})
        link = SKILLS_PLUGIN / "skills"
        if not (link.is_symlink() and os.readlink(link) == str(skills)):
            if link.is_symlink() or link.exists():
                link.unlink()
            link.symlink_to(skills)
        plugin_dir = str(SKILLS_PLUGIN)
    return {"settings": str(SETTINGS_FILE), "plugin_dir": plugin_dir}


def mcp_config(path: Path, servers: dict | None = None) -> str:
    """--mcp-config file: {} (no MCP at all) or just Tabbie's delegate server."""
    write_json(path, {"mcpServers": servers or {}})
    return str(path)


def profile_for(ecfg: dict, prompt: str = "") -> str:
    p = str(ecfg.get("worker_profile", "full")).lower()
    if p == "lean" and prompt and FULL_PROFILE_RX.search(prompt):
        return "full"
    return "lean" if p == "lean" else "full"
