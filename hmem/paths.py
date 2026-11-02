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


def is_link(p: Path) -> bool:
    """A symlink, or (Windows) a directory junction."""
    return p.is_symlink() or bool(getattr(p, "is_junction", lambda: False)())


def read_link(p: Path) -> str:
    t = os.readlink(p)
    return t[4:] if t.startswith("\\\\?\\") else t  # junction targets come back as \\?\C:\...


def link_dir(link: Path, target: str | Path) -> None:
    """link -> target folder. Windows only allows symlinks with Developer Mode or admin, so fall back
    to a directory junction there (same effect for a local folder, no rights needed)."""
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        if sys.platform != "win32":
            raise
        import subprocess
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
        if r.returncode != 0:
            raise OSError(f"couldn't link {link} -> {target}") from None


def unlink(p: Path) -> None:
    try:
        p.unlink()
    except (IsADirectoryError, PermissionError):
        os.rmdir(p)  # a junction on some Python versions
