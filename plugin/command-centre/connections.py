"""What this Tabbie is connected to, for Settings → Providers (read-only, never a key or token).

  workers   Claude Code and Codex: installed, signed in to which subscription plan (Claude Max, ChatGPT
            Plus...). Read from `claude auth status` (JSON) and `codex login status` + the plan claim in
            ~/.codex/auth.json's id_token, decoded locally. Cached 10 minutes.
  services  other accounts the setup can use: MCP servers in Claude Code (~/.claude.json, enabled
            plugins), Codex (~/.codex/config.toml) and the hub (~/.hermes/config.yaml), plus the
            Higgsfield / ElevenLabs command-line tools. "Set up" only says where it's configured; it
            doesn't call the service.
  providers model providers linked in Tabbie's model picker (config/providers.yaml + a key).

TABBIE_LINKED=claude,codex (or empty) overrides the sign-in check, like providers.linked (tests).
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .common import _load_yaml, hermes_home

TTL = 600
_cache: dict = {}
_lock = threading.Lock()

PLAN_NAMES = {"pro": "Pro", "max": "Max", "team": "Team", "enterprise": "Enterprise", "free": "Free",
              "plus": "Plus", "business": "Business", "edu": "Edu"}

WORKERS = {
    "claude": {"name": "Claude Code", "account": "Claude", "manage_url": "https://claude.ai/settings/billing",
               "signin": "claude auth login"},
    "codex": {"name": "Codex", "account": "ChatGPT", "manage_url": "https://chatgpt.com/#settings/Subscription",
              "signin": "codex login"},
}

# Services people recognise. Anything else found in an MCP config is listed under its own name.
KNOWN = {
    "higgsfield": {"name": "Higgsfield", "what": "Images, video and audio", "url": "https://higgsfield.ai", "cli": "higgsfield"},
    "elevenlabs": {"name": "ElevenLabs", "what": "Voices, speech and sound", "url": "https://elevenlabs.io/app", "cli": "elevenlabs"},
    "telegram": {"name": "Telegram", "what": "Your Telegram chats"},
    "figma": {"name": "Figma", "what": "Design files", "url": "https://www.figma.com"},
    "vercel": {"name": "Vercel", "what": "Deploys and hosting", "url": "https://vercel.com/dashboard"},
    "github": {"name": "GitHub", "what": "Repos, issues and pull requests", "url": "https://github.com"},
    "rea": {"name": "REA", "what": "Reverse-engineering tools"},
    "notion": {"name": "Notion", "what": "Pages and databases", "url": "https://www.notion.so"},
    "linear": {"name": "Linear", "what": "Issues and projects", "url": "https://linear.app"},
    "slack": {"name": "Slack", "what": "Slack messages", "url": "https://slack.com"},
}
WHERE = {"claude": "Claude Code", "codex": "Codex", "hub": "Hub", "cli": "Command line"}


# --- workers ---------------------------------------------------------------------------------------

def _plan(raw: str, account: str) -> str:
    raw = (raw or "").strip().lower()
    if not raw:
        return ""
    return f"{account} {PLAN_NAMES.get(raw, raw.replace('_', ' ').title())}"


def jwt_claims(token: str) -> dict:
    """The payload of a JWT, decoded locally (no signature check: only used to read the plan name)."""
    try:
        part = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except Exception:
        return {}


def codex_plan(auth_file: Path | None = None) -> str:
    f = auth_file or Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "auth.json"
    try:
        tokens = (json.loads(f.read_text(encoding="utf-8")).get("tokens") or {})
    except (OSError, ValueError):
        return ""
    claims = jwt_claims(str(tokens.get("id_token") or ""))
    return _plan(str((claims.get("https://api.openai.com/auth") or {}).get("chatgpt_plan_type") or ""), "ChatGPT")


def parse_claude_status(out: str) -> tuple[bool | None, str, str]:
    """`claude auth status` JSON -> (signed in to a Claude plan, plan label, note)."""
    try:
        st = json.loads(out)
    except (ValueError, TypeError):
        return None, "", "Couldn't read Claude Code's sign-in status."
    if not st.get("loggedIn"):
        return False, "", "Not signed in."
    if st.get("authMethod") != "claude.ai" or st.get("apiProvider") != "firstParty":
        return False, "", "Signed in with an API key or another provider, not a Claude plan."
    return True, _plan(str(st.get("subscriptionType") or ""), "Claude") or "Claude plan", ""


def _check(agent: str) -> dict:
    from . import providers
    forced = os.environ.get("TABBIE_LINKED")
    info = WORKERS[agent]
    out = {"id": agent, "name": info["name"], "account": info["account"], "manage_url": info["manage_url"],
           "signin": info["signin"], "installed": bool(shutil.which(agent)), "signed_in": False, "plan": "", "note": ""}
    if forced is not None:
        out["signed_in"] = agent in {x.strip() for x in forced.split(",") if x.strip()}
        return out
    if not out["installed"]:
        out["note"] = "Not installed."
        return out
    if agent == "claude":
        code, so, _ = providers._run(["claude", "auth", "status"], cwd=str(Path.home()),
                                     env=providers._clean_env(providers._ANTHROPIC_BILLING_VARS), timeout=8)
        out["signed_in"], out["plan"], out["note"] = parse_claude_status(so)
    else:
        code, so, se = providers._run(["codex", "login", "status"], cwd=str(Path.home()),
                                      env=providers._clean_env(providers._OPENAI_BILLING_VARS), timeout=8)
        text = (so + se).strip()
        if "ChatGPT" in text:
            out["signed_in"], out["plan"] = True, codex_plan() or "ChatGPT plan"
        elif code == 124:
            out["signed_in"], out["note"] = None, "Couldn't check just now."
        elif "API key" in text:
            out["note"] = "Signed in with an API key, not a ChatGPT plan."
        else:
            out["note"] = "Not signed in."
    return out


def workers(refresh: bool = False) -> list[dict]:
    with _lock:
        hit = _cache.get("workers")
        if hit and not refresh and time.time() - hit[0] < TTL:
            return hit[1]
    with ThreadPoolExecutor(2) as ex:
        res = list(ex.map(_safe_check, WORKERS))
    try:
        from .common import agents
        on = set(agents().enabled())
    except Exception:
        on = set(WORKERS)
    for w in res:
        w["enabled"] = w["id"] in on
    if all(w["signed_in"] is not None for w in res):  # an unsure answer is asked again next time
        with _lock:
            _cache["workers"] = (time.time(), res)
    return res


def _safe_check(agent: str) -> dict:
    try:
        return _check(agent)
    except Exception as exc:
        sys.stderr.write(f"cc-sidecar connections: {agent}: {exc!r}\n")
        info = WORKERS[agent]
        return {"id": agent, "name": info["name"], "account": info["account"], "manage_url": info["manage_url"],
                "signin": info["signin"], "installed": bool(shutil.which(agent)), "signed_in": None, "plan": "",
                "note": "Couldn't check just now."}


# --- services --------------------------------------------------------------------------------------

def _key(name: str) -> str:
    """MCP server / plugin name -> service id: 'figma@claude-plugins-official' -> 'figma'."""
    n = str(name).split("@", 1)[0].strip().lower()
    for k in KNOWN:
        if n == k or n.startswith(k + "-") or n.startswith(k + "_") or n.endswith("-" + k):
            return k
    return n


def _json(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _toml(path: Path) -> dict:
    try:
        import tomllib
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def found_services(home: Path | None = None, hermes: Path | None = None, which=shutil.which) -> dict[str, set]:
    """service id -> where it's set up ({'claude', 'codex', 'hub', 'cli'}). Names only, never config values."""
    home = home or Path.home()
    hermes = hermes or hermes_home()
    found: dict[str, set] = {}
    add = lambda name, where: found.setdefault(_key(name), set()).add(where)  # noqa: E731
    for name in (_json(home / ".claude.json").get("mcpServers") or {}):
        add(name, "claude")
    for name in (_json(home / ".claude" / "settings.json").get("mcpServers") or {}):
        add(name, "claude")
    for plugin, on in (_json(home / ".claude" / "settings.json").get("enabledPlugins") or {}).items():
        if on and _key(plugin) in KNOWN:  # skill packs aren't services; plugins for a known service are
            add(plugin, "claude")
    codex_home = Path(os.environ.get("CODEX_HOME", home / ".codex"))
    for name in (_toml(codex_home / "config.toml").get("mcp_servers") or {}):
        add(name, "codex")
    try:
        hub = _load_yaml(hermes / "config.yaml").get("mcp_servers") or {}
    except Exception:
        hub = {}
    for name in (hub if isinstance(hub, dict) else {}):
        add(name, "hub")
    for k, info in KNOWN.items():
        if info.get("cli") and which(info["cli"]):
            add(k, "cli")
    return found


def services(home: Path | None = None, hermes: Path | None = None, which=shutil.which) -> list[dict]:
    out = []
    for k, where in found_services(home, hermes, which).items():
        info = KNOWN.get(k, {})
        out.append({"id": k, "name": info.get("name") or k.replace("_", " ").replace("-", " ").title(),
                    "what": info.get("what", ""), "url": info.get("url", ""),
                    "where": [WHERE[w] for w in ("claude", "codex", "hub", "cli") if w in where]})
    # known services first (alphabetical), then the rest
    return sorted(out, key=lambda s: (s["id"] not in KNOWN, s["name"].lower()))


def model_providers_linked() -> list[dict]:
    try:
        from . import model_providers as mp
        out = []
        for pid in mp.linked_ids():
            p = mp.get(pid) or {}
            out.append({"id": pid, "name": p.get("name") or pid, "billing": mp.BILLING_LABEL.get(p.get("billing"), ""),
                        "worker": WORKERS.get(p.get("worker", "claude"), {}).get("name", "")})
        return out
    except Exception as exc:
        sys.stderr.write(f"cc-sidecar connections: providers: {exc!r}\n")
        return []


def payload(refresh: bool = False) -> dict:
    return {"workers": workers(refresh), "services": services(), "providers": model_providers_linked()}
