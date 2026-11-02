"""Model providers beyond Claude Code and Codex: any service that speaks the Anthropic Messages API
(DeepSeek, Kimi, GLM, MiniMax, Qwen, OpenRouter, a local Ollama, or a custom endpoint). Their models run
inside Claude Code, pointed at the provider (ANTHROPIC_BASE_URL + the provider's key), so every Tabbie
feature (narration, Stop, questions, reviews, hand-offs) works the same.

  config/providers.yaml   which providers are on, custom ones, cached model lists. No secrets.
  macOS Keychain          the keys (service "tabbie-provider", account = provider id). Never in git,
                          logs, config or a command line.

Billing is explicit: "subscription" (a flat plan, e.g. Kimi Code), "pay-per-use" (an API key billed per
token; the picker labels it) or "local" (free, on this computer).
"""
from __future__ import annotations

import json
import re
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from .common import ROOT, _dump_yaml, _load_yaml

CONFIG = ROOT / "config" / "providers.yaml"
KEYCHAIN_SERVICE = "tabbie-provider"
MODELS_TTL = 6 * 3600
_lock = threading.Lock()

# Model tiers decide hand-off and takeover peers: a job moves to the same tier on another provider.
TIERS = ("top", "mid", "fast")

# Presets. Model ids drift: each provider's list is fetched live (models_url) and these are the fallback
# and the labels/tiers for ids we know.
PRESETS: dict[str, dict] = {
    # The same Claude / GPT models, with an API key instead of the Claude or ChatGPT subscription login.
    "anthropic-api": {
        "name": "Anthropic API key", "worker": "claude", "base_url": "https://api.anthropic.com", "billing": "pay-per-use",
        "official": True, "models_url": "https://api.anthropic.com/v1/models",
        "key_url": "https://console.anthropic.com/settings/keys",
        "models": [{"id": "claude-opus-5-5", "name": "Opus 5.5", "tier": "top"},
                   {"id": "claude-sonnet-5-5", "name": "Sonnet 5.5", "tier": "mid"},
                   {"id": "claude-haiku-5-5", "name": "Haiku 5.5", "tier": "fast"}],
    },
    "openai-api": {
        "name": "OpenAI API key", "worker": "codex", "base_url": "https://api.openai.com/v1", "billing": "pay-per-use",
        "official": True, "models_url": "https://api.openai.com/v1/models", "key_url": "https://platform.openai.com/api-keys",
        "models": [{"id": "gpt-6-astra", "name": "GPT-6 Astra", "tier": "top"},
                   {"id": "gpt-6.1-sol", "name": "GPT-6.1 Sol", "tier": "mid"},
                   {"id": "gpt-6-luna", "name": "GPT-6 Luna", "tier": "fast"}],
    },
    "deepseek": {
        "name": "DeepSeek", "base_url": "https://api.deepseek.com/anthropic", "billing": "pay-per-use",
        "models_url": "https://api.deepseek.com/models", "key_url": "https://platform.deepseek.com/api_keys",
        "models": [{"id": "deepseek-v4-pro", "name": "DeepSeek V4 Pro", "tier": "top"},
                   {"id": "deepseek-flash", "name": "DeepSeek Flash", "tier": "fast"}],
    },
    "kimi-code": {
        "name": "Kimi Code", "base_url": "https://api.kimi.com/coding/", "billing": "subscription",
        "key_url": "https://www.kimi.com/code",
        "models": [{"id": "kimi-for-coding", "name": "Kimi for Coding", "tier": "top"}],
    },
    "kimi": {
        "name": "Kimi (Moonshot API)", "base_url": "https://api.moonshot.ai/anthropic", "billing": "pay-per-use",
        "models_url": "https://api.moonshot.ai/v1/models", "key_url": "https://platform.moonshot.ai/console/api-keys",
        "models": [{"id": "kimi-k2.6", "name": "Kimi K2.6", "tier": "top"}],
    },
    "glm": {
        "name": "GLM (Z.ai)", "base_url": "https://api.z.ai/api/anthropic", "billing": "subscription",
        "models_url": "https://api.z.ai/api/paas/v4/models", "key_url": "https://z.ai/manage-apikey/apikey-list",
        "models": [{"id": "glm-5.2", "name": "GLM-5.2", "tier": "top"},
                   {"id": "glm-5-air", "name": "GLM-5 Air", "tier": "fast"}],
    },
    "minimax": {
        "name": "MiniMax", "base_url": "https://api.minimax.io/anthropic", "billing": "pay-per-use",
        "key_url": "https://www.minimax.io/platform",
        "models": [{"id": "MiniMax-M2.7", "name": "MiniMax M2.7", "tier": "top"}],
    },
    "qwen": {
        "name": "Qwen (Alibaba)", "base_url": "https://dashscope-intl.aliyuncs.com/apps/anthropic", "billing": "pay-per-use",
        "models_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/models",
        "key_url": "https://modelstudio.console.alibabacloud.com/",
        "models": [{"id": "qwen3-coder-plus", "name": "Qwen3 Coder Plus", "tier": "top"},
                   {"id": "qwen-flash", "name": "Qwen Flash", "tier": "fast"}],
    },
    "openrouter": {
        "name": "OpenRouter", "base_url": "https://openrouter.ai/api", "billing": "pay-per-use",
        "models_url": "https://openrouter.ai/api/v1/models", "key_url": "https://openrouter.ai/settings/keys",
        "models": [],
    },
    "ollama": {
        "name": "Ollama (this computer)", "base_url": "http://127.0.0.1:11434", "billing": "local", "auth": "none",
        "models_url": "http://127.0.0.1:11434/api/tags", "key_url": "https://ollama.com/download",
        "models": [],
    },
}
BILLING_LABEL = {"subscription": "Plan", "pay-per-use": "Pay per use", "local": "On this computer"}
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,30}$")
_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,120}$")


# ---------------------------------------------------------------- config (no secrets)

def _load() -> dict:
    try:
        data = _load_yaml(CONFIG)
    except Exception:
        data = {}
    return data if isinstance(data, dict) else {}


def _save(data: dict) -> None:
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    _dump_yaml(CONFIG, data)


def _valid_url(url: str, *, local_ok: bool = False) -> bool:
    u = urlsplit(str(url or ""))
    if u.scheme == "https" and u.hostname:
        return True
    return local_ok and u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost", "::1")


def all_providers() -> dict[str, dict]:
    """Presets + the user's custom providers, each with its id."""
    data = _load()
    out = {pid: {"worker": "claude", **p, "id": pid, "custom": False} for pid, p in PRESETS.items()}
    for pid, c in (data.get("custom") or {}).items():
        if _ID.match(str(pid)) and isinstance(c, dict) and _valid_url(c.get("base_url"), local_ok=True):
            out[pid] = {"id": pid, "custom": True, "name": str(c.get("name") or pid)[:40],
                        "worker": "codex" if c.get("worker") == "codex" else "claude",
                        "base_url": c["base_url"], "billing": c.get("billing") if c.get("billing") in BILLING_LABEL else "pay-per-use",
                        "models_url": c.get("models_url") if _valid_url(c.get("models_url"), local_ok=True) else None,
                        "auth": "none" if c.get("auth") == "none" else "key",
                        "models": [m for m in (c.get("models") or []) if isinstance(m, dict) and _MODEL.match(str(m.get("id") or ""))]}
    return out


def get(pid: str) -> dict | None:
    return all_providers().get(pid)


def enabled_ids() -> list[str]:
    on = _load().get("enabled") or []
    return [p for p in on if p in all_providers()]


def set_enabled(pid: str, on: bool) -> None:
    with _lock:
        data = _load()
        cur = [p for p in (data.get("enabled") or []) if p != pid]
        if on:
            cur.append(pid)
        data["enabled"] = cur
        _save(data)


def add_custom(pid: str, name: str, base_url: str, models: list[str], *, billing: str = "pay-per-use",
               models_url: str | None = None, auth: str = "key", worker: str = "claude") -> dict:
    """worker claude: an Anthropic Messages endpoint, run in Claude Code. worker codex: an OpenAI
    Responses API endpoint, run in Codex (Codex no longer speaks Chat Completions)."""
    if not _ID.match(pid) or pid in PRESETS or pid in ("anthropic", "openai"):
        raise ValueError("pick a short id: lowercase letters, digits and dashes")
    if not _valid_url(base_url, local_ok=True):
        raise ValueError("the base URL must be https:// (or http://127.0.0.1 for a local server)")
    ms = [m.strip() for m in models if _MODEL.match(m.strip())]
    with _lock:
        data = _load()
        data.setdefault("custom", {})[pid] = {
            "name": str(name or pid)[:40], "base_url": base_url.rstrip("/"), "worker": "codex" if worker == "codex" else "claude", "billing": billing if billing in BILLING_LABEL else "pay-per-use",
            "models_url": models_url if models_url and _valid_url(models_url, local_ok=True) else None,
            "auth": "none" if auth == "none" else "key",
            "models": [{"id": m, "name": m, "tier": guess_tier(m)} for m in ms]}
        _save(data)
    return get(pid)


def remove(pid: str) -> None:
    delete_key(pid)
    with _lock:
        data = _load()
        (data.get("custom") or {}).pop(pid, None)
        data["enabled"] = [p for p in (data.get("enabled") or []) if p != pid]
        (data.get("models_cache") or {}).pop(pid, None)
        _save(data)


# ---------------------------------------------------------------- keys (OS credential store)
# macOS: the login Keychain through the built-in `security` tool. Windows: Credential Manager
# (hmem/wincred.py). Anything else: the `keyring` library. Never a file, log or command line.

def _use_security() -> bool:
    import shutil
    import sys
    return sys.platform == "darwin" and bool(shutil.which("security"))


def _keyring():
    import sys
    if sys.platform == "win32":
        from .procutil import procutil  # noqa: F401  (puts ~/hermes on sys.path)
        from hmem import wincred  # Credential Manager straight through the Win32 API: no package needed
        return wincred
    try:
        import keyring
        return keyring
    except Exception:
        return None


def _security(script: str, timeout: int = 15) -> subprocess.CompletedProcess:
    """`security -i` reads its commands from stdin: the key never appears in a command line (ps)."""
    return subprocess.run(["security", "-i"], input=script, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


def _q(s: str) -> str:
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def set_key(pid: str, key: str) -> None:
    key = str(key or "").strip()
    if not key or len(key) > 400 or any(c in key for c in "\r\n"):
        raise ValueError("that doesn't look like an API key")
    if _use_security():
        r = _security(f"add-generic-password -U -s {_q(KEYCHAIN_SERVICE)} -a {_q(pid)} -l {_q('Tabbie: ' + pid)} -w {_q(key)}\n")
        if r.returncode != 0 or "error" in (r.stderr or "").lower():
            raise RuntimeError("couldn't save the key in the Keychain")
        _HAS.pop(pid, None)
        return
    kr = _keyring()
    if not kr:
        raise RuntimeError("no credential store: install the keyring package (bin/setup does)")
    kr.set_password(KEYCHAIN_SERVICE, pid, key)
    _HAS.pop(pid, None)


def get_key(pid: str) -> str | None:
    if _use_security():
        try:
            r = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", pid, "-w"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
        except (OSError, subprocess.SubprocessError):
            return None
        k = r.stdout.strip()
        return k if r.returncode == 0 and k else None
    kr = _keyring()
    try:
        return (kr.get_password(KEYCHAIN_SERVICE, pid) or None) if kr else None
    except Exception:
        return None


def delete_key(pid: str) -> None:
    _HAS.pop(pid, None)
    if _use_security():
        try:
            subprocess.run(["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", pid],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
        except (OSError, subprocess.SubprocessError):
            pass
        return
    kr = _keyring()
    try:
        if kr:
            kr.delete_password(KEYCHAIN_SERVICE, pid)
    except Exception:
        pass


_HAS: dict[str, tuple[float, bool]] = {}


def has_key(pid: str) -> bool:
    """Cached for a minute: the picker and routing ask often, and each Keychain read is a process."""
    hit = _HAS.get(pid)
    if hit and hit[0] > time.time() - 60:
        return hit[1]
    ok = get_key(pid) is not None
    _HAS[pid] = (time.time(), ok)
    return ok


# ---------------------------------------------------------------- models

def guess_tier(model_id: str) -> str:
    m = model_id.lower()
    if re.search(r"(flash|mini|lite|turbo|haiku|small|air|nano|instant|fast|8b|7b|3b)", m):
        return "fast"
    if re.search(r"(pro|max|opus|large|ultra|plus|coder|k2|reasoner|405b|70b|thinking)", m):
        return "top"
    return "mid"


def _get_json(url: str, key: str | None, timeout: int = 12):
    req = urllib.request.Request(url, headers={"User-Agent": "Tabbie", "Accept": "application/json", "anthropic-version": "2023-06-01",
                                               **({"Authorization": f"Bearer {key}", "x-api-key": key} if key else {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (URL is a preset or validated)
        return json.loads(r.read(4_000_000) or b"{}")


def fetch_models(pid: str) -> list[dict]:
    """The provider's live model list (OpenAI / Anthropic /models shape, or Ollama's /api/tags), merged
    with the preset's labels. Falls back to the preset list."""
    p = get(pid)
    if not p:
        return []
    known = {m["id"]: m for m in p.get("models") or []}
    ids: list[str] = []
    url = p.get("models_url") or (p["base_url"].rstrip("/") + "/v1/models")
    try:
        data = _get_json(url, None if p.get("auth") == "none" else get_key(pid))
        rows = data.get("data") or data.get("models") or [] if isinstance(data, dict) else data
        for row in rows or []:
            mid = str((row.get("id") or row.get("name") or row.get("model")) if isinstance(row, dict) else row)
            if pid == "openrouter" and isinstance(row, dict) and "tools" not in (row.get("supported_parameters") or ["tools"]):
                continue  # Claude Code needs tool use
            if p.get("official") and not re.match(r"^(claude-|gpt-|o\d|codex)", mid):
                continue  # the official lists include embeddings, images, audio...
            if _MODEL.match(mid) and mid not in ids:
                ids.append(mid)
    except Exception:
        ids = []
    out = [known[i] for i in known if not ids or i in ids]
    out += [{"id": i, "name": i, "tier": guess_tier(i)} for i in ids if i not in known]
    for m in out:
        m.setdefault("tier", guess_tier(m["id"]))
    return out or list(known.values())


def check(pid: str) -> tuple[bool, str]:
    """Ask the provider for its model list with the saved key: (works, a line for the user)."""
    import urllib.error
    p = get(pid)
    if not p:
        return False, "unknown provider"
    key = None if p.get("auth") == "none" else get_key(pid)
    if p.get("auth") != "none" and not key:
        return False, "no key saved"
    try:
        _get_json(p.get("models_url") or (p["base_url"].rstrip("/") + "/v1/models"), key, timeout=10)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return False, f"{p['name']} rejected the key ({exc.code})"
        if exc.code == 404:  # some endpoints have no model list: the key can't be checked this way
            return True, "saved (this provider has no model list to check the key against)"
        return False, f"{p['name']} answered {exc.code}"
    except Exception as exc:
        return False, f"couldn't reach {p['name']}: {type(exc).__name__}"
    models(pid, refresh=True)
    return True, "works"


def models(pid: str, refresh: bool = False) -> list[dict]:
    """Cached model list (MODELS_TTL); a refresh asks the provider again."""
    data = _load()
    cache = (data.get("models_cache") or {}).get(pid) or {}
    if not refresh and cache.get("at", 0) > time.time() - MODELS_TTL and cache.get("models"):
        return cache["models"]
    got = fetch_models(pid)
    if got:
        with _lock:
            data = _load()
            data.setdefault("models_cache", {})[pid] = {"at": time.time(), "models": got[:400]}
            _save(data)
    return got or cache.get("models") or []


def model_info(pid: str, model: str) -> dict | None:
    for m in models(pid):
        if m["id"] == model:
            return m
    p = get(pid)
    if p and _MODEL.match(model or ""):  # a model the user typed that the list doesn't show (yet)
        return {"id": model, "name": model, "tier": guess_tier(model)}
    return None


# ---------------------------------------------------------------- linked + running

def linked(pid: str) -> bool:
    """Usable now: enabled, and its key is in the Keychain (or it needs none and answers)."""
    p = get(pid)
    if not p or pid not in enabled_ids():
        return False
    if p.get("auth") == "none":  # a local server: is it up? (cached a minute)
        hit = _HAS.get("up:" + pid)
        if hit and hit[0] > time.time() - 60:
            return hit[1]
        try:
            _get_json(p.get("models_url") or p["base_url"], None, timeout=3)
            up = True
        except Exception:
            up = False
        _HAS["up:" + pid] = (time.time(), up)
        return up
    return has_key(pid)


def linked_ids() -> list[str]:
    return [p for p in enabled_ids() if linked(p)]


def claude_env(pid: str, model: str) -> dict:
    """Environment that points Claude Code at this provider and model (main, subagents and the small
    background model all use it). Raises when the key is missing."""
    p = get(pid)
    if not p:
        raise ValueError(f"unknown provider {pid}")
    key = None if p.get("auth") == "none" else get_key(pid)
    if p.get("auth") != "none" and not key:
        raise RuntimeError(f"{p['name']} has no key in the Keychain (model picker → {p['name']} → Add key)")
    if p.get("worker") != "claude":
        raise ValueError(f"{p['name']} runs in Codex, not Claude Code")
    if p.get("official"):  # Anthropic itself, with an API key instead of the subscription login
        return {"ANTHROPIC_API_KEY": key, "TABBIE_MODEL_PROVIDER": pid}
    fast = next((m["id"] for m in models(pid) if m.get("tier") == "fast"), model)
    env = {"ANTHROPIC_BASE_URL": p["base_url"].rstrip("/"), "ANTHROPIC_MODEL": model,
           "ANTHROPIC_DEFAULT_OPUS_MODEL": model, "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
           "ANTHROPIC_DEFAULT_HAIKU_MODEL": fast, "ANTHROPIC_SMALL_FAST_MODEL": fast,
           "CLAUDE_CODE_SUBAGENT_MODEL": model, "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
           "TABBIE_MODEL_PROVIDER": pid}
    env["ANTHROPIC_AUTH_TOKEN"] = key or "ollama"
    return env


def billing_text(pid: str) -> str:
    p = get(pid) or {}
    return {"subscription": f"{p.get('name', pid)} plan", "local": f"{p.get('name', pid)} on this computer"}.get(
        p.get("billing"), f"{p.get('name', pid)} (pay per use)")


def codex_launch(pid: str) -> tuple[list[str], dict]:
    """Extra `codex exec` args and environment for a Codex-run provider: the OpenAI API with a key, or a
    custom Responses API endpoint. The key only travels in the environment."""
    p = get(pid)
    if not p or p.get("worker") != "codex":
        raise ValueError(f"{pid} doesn't run in Codex")
    key = None if p.get("auth") == "none" else get_key(pid)
    if p.get("auth") != "none" and not key:
        raise RuntimeError(f"{p['name']} has no key saved (model picker → {p['name']} → Add key)")
    if p.get("official"):
        return [], {"CODEX_API_KEY": key, "OPENAI_API_KEY": key, "TABBIE_MODEL_PROVIDER": pid}
    name = "tabbie_" + re.sub(r"[^a-z0-9]", "_", pid)
    spec = (f'{{name="{p["name"]}",base_url="{p["base_url"]}",env_key="TABBIE_PROVIDER_KEY",wire_api="responses"}}')
    return (["-c", f'model_provider="{name}"', "-c", f"model_providers.{name}={spec}"],
            {"TABBIE_PROVIDER_KEY": key or "local", "TABBIE_MODEL_PROVIDER": pid})


def public(pid: str, *, with_models: bool = True) -> dict:
    p = get(pid) or {}
    return {"id": pid, "name": p.get("name"), "worker": p.get("worker", "claude"), "billing": p.get("billing"), "billing_label": BILLING_LABEL.get(p.get("billing"), ""),
            "custom": bool(p.get("custom")), "auth": p.get("auth") or "key", "key_url": p.get("key_url"),
            "enabled": pid in enabled_ids(), "linked": linked(pid), "base_url": p.get("base_url"),
            **({"models": models(pid)} if with_models else {})}
