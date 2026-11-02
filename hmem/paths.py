"""Where Hermes keeps its files on this OS. Hermes Agent's own default: ~/.hermes on macOS / Linux,
%LOCALAPPDATA%\\hermes on native Windows. HERMES_HOME / HERMES_WEBUI_STATE_DIR override both."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def hermes_home() -> Path:
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env).expanduser()
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "hermes"
    return Path.home() / ".hermes"


def webui_state() -> Path:
    env = os.environ.get("HERMES_WEBUI_STATE_DIR")
    return Path(env).expanduser() if env else hermes_home() / "webui"
