"""Hub sign-in from the WebUI ("Finish setting up Tabbie" card, webui-extension/hub-signin.js).

The WebUI's own OAuth endpoints do the sign-in (ChatGPT device code, or linking Claude Code's login).
With its first-run wizard switched off they won't write the hub's provider, so this does: it points
Hermes' model at that login, after backing up Hermes' config. Only two providers, both subscription
logins; never an API key.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from .procutil import procutil
from .common import ROOT, _load_yaml, hermes_home

MODELS = {"openai-codex": "gpt-6.1-sol", "anthropic": "claude-sonnet-5-5"}
# Settings → Preferences offers the newest model of each family (model_latest), for either login.
FAMILY = {"anthropic": ("anthropic", ("opus", "fable", "sonnet", "haiku")),
          "openai-codex": ("openai", ("sol", "astra", "terra", "luna"))}


def _latest():
    from . import model_latest
    return model_latest


def model_ok(provider: str, model: str) -> bool:
    """A model id in one of this login's families (claude-* for Claude, gpt-*-<family> for ChatGPT)."""
    p = _latest().parse(model or "")
    fam, names = FAMILY.get(provider, ("", ()))
    return bool(p) and p[0] == fam and p[1] in names


def options() -> dict:
    """GET /hub: the hub's current login + model, whether Claude Code's login is linked to the hub yet,
    and the newest model per family for each login. Names only, never a token."""
    from .executor_choice import CATALOG
    ml = _latest()
    fams = ml.families()
    out: dict = {}
    for provider, (key, names) in FAMILY.items():
        got = fams.get(key) or {}
        builtin = {ml.parse(m["id"])[1]: m["id"] for m in CATALOG[key]["models"] if ml.parse(m["id"])}
        rows = []
        for n in names:
            mid = (got.get(n) or {}).get("id") or ml.upgrade(builtin.get(n))
            if mid:
                rows.append({"id": mid, "name": (got.get(n) or {}).get("name") or ml.display_name(mid), "family": n})
        out[provider] = rows
    try:
        cfg = _load_yaml(hermes_home() / "config.yaml").get("model") or {}
    except Exception:
        cfg = {}
    linked = False
    try:
        import json
        pool = (json.loads((hermes_home() / "auth.json").read_text(encoding="utf-8")).get("credential_pool") or {})
        linked = any(isinstance(e, dict) and e.get("source") == "claude_code_linked" for e in pool.get("anthropic") or [])
    except (OSError, ValueError):
        pass
    return {"current": {"provider": str(cfg.get("provider") or ""), "model": str(cfg.get("default") or "")},
            "claude_linked": linked, "models": out}


def _hermes(*args: str, timeout: int = 20) -> tuple[int, str]:
    exe = shutil.which("hermes") or str(hermes_home() / "bin" / "hermes")
    try:
        r = subprocess.run([exe, *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, stdin=subprocess.DEVNULL)
        return r.returncode, (r.stdout or "").strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)


def use(provider: str, model: str | None = None) -> dict:
    if provider not in MODELS:
        raise ValueError("provider must be openai-codex or anthropic")
    if model and not model_ok(provider, model):
        raise ValueError("unknown model for that login")
    model = model or _latest().upgrade(MODELS[provider]) or MODELS[provider]
    code, cfg = _hermes("config", "path")
    if code == 0 and cfg and Path(cfg).exists():
        bk = ROOT / "backups" / f"hub-signin-{time.strftime('%Y%m%d-%H%M%S')}"
        bk.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cfg, bk / Path(cfg).name)
    for key, val in (("model.provider", provider), ("model.default", model)):
        code, out = _hermes("config", "set", key, val)
        if code:
            raise RuntimeError(f"couldn't save the hub model ({out[-160:]})")
    return {"ok": True, "provider": provider, "model": model}


def claude_login() -> dict:
    """Start Claude Code's own sign-in on this computer (opens claude.ai in that computer's browser)."""
    exe = procutil.local_bin("claude")
    if not shutil.which(exe) and not os.access(exe, os.X_OK):
        return {"ok": False, "error": "Claude Code isn't installed on this computer. Run bin/setup there."}
    subprocess.Popen([exe, "auth", "login", "--claudeai"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, **procutil.DETACHED)
    return {"ok": True}
