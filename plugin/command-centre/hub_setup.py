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

from .common import ROOT

MODELS = {"openai-codex": "gpt-6.1-sol", "anthropic": "claude-sonnet-5-5"}


def _hermes(*args: str, timeout: int = 20) -> tuple[int, str]:
    exe = shutil.which("hermes") or str(Path.home() / ".hermes" / "bin" / "hermes")
    try:
        r = subprocess.run([exe, *args], capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return r.returncode, (r.stdout or "").strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)


def use(provider: str) -> dict:
    if provider not in MODELS:
        raise ValueError("provider must be openai-codex or anthropic")
    code, cfg = _hermes("config", "path")
    if code == 0 and cfg and Path(cfg).exists():
        bk = ROOT / "backups" / f"hub-signin-{time.strftime('%Y%m%d-%H%M%S')}"
        bk.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cfg, bk / Path(cfg).name)
    for key, val in (("model.provider", provider), ("model.default", MODELS[provider])):
        code, out = _hermes("config", "set", key, val)
        if code:
            raise RuntimeError(f"couldn't save the hub model ({out[-160:]})")
    return {"ok": True, "provider": provider, "model": MODELS[provider]}


def claude_login() -> dict:
    """Start Claude Code's own sign-in on this Mac (opens claude.ai in the Mac's browser)."""
    exe = shutil.which("claude") or str(Path.home() / ".local" / "bin" / "claude")
    if not os.access(exe, os.X_OK):
        return {"ok": False, "error": "Claude Code isn't installed on this Mac. Run bin/setup there."}
    subprocess.Popen([exe, "auth", "login", "--claudeai"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    return {"ok": True}
