"""Shared paths, config loading and small JSON-state helpers."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from .flockcompat import fcntl
from hmem.paths import hermes_home, webui_state  # noqa: E402  (flockcompat put ~/hermes on sys.path)

ROOT = Path(os.environ.get("COMMAND_CENTRE_HOME", Path.home() / "hermes")).expanduser()
CONFIG_DIR = ROOT / "config"
STATE_DIR = ROOT / "state"
REQUESTS_DIR = STATE_DIR / "requests"
SESSIONS_DIR = STATE_DIR / "sessions"
LOCKS_DIR = STATE_DIR / "locks"
LOG_FILE = STATE_DIR / "decisions.jsonl"


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # PyYAML

        return yaml.safe_load(text) or {}
    except ImportError:
        from ruamel.yaml import YAML

        return YAML(typ="safe").load(text) or {}


def _dump_yaml(path: Path, data: dict) -> None:
    try:
        import yaml

        text = yaml.safe_dump(data, sort_keys=False)
    except ImportError:
        import io

        from ruamel.yaml import YAML

        buf = io.StringIO()
        YAML().dump(data, buf)
        text = buf.getvalue()
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")  # never a half-written file
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        if path.exists():
            os.chmod(tmp, path.stat().st_mode & 0o777)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def load_config() -> dict:
    return _load_yaml(CONFIG_DIR / "router.yaml")


def load_projects_file() -> dict:
    return _load_yaml(CONFIG_DIR / "projects.yaml")


def save_projects_file(data: dict) -> None:
    _dump_yaml(CONFIG_DIR / "projects.yaml", data)


@contextlib.contextmanager
def projects_file_lock():
    """Hold around load_projects_file() ... save_projects_file() so two writers don't drop each other's change."""
    lf = CONFIG_DIR / ".projects.yaml.lock"
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(lf, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: Path, data: Any) -> None:
    """Atomic write so a crash never leaves half a state file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, default=str)
    os.replace(tmp, path)


def log_decision(**fields: Any) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    fields.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S"))
    with LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(fields, default=str) + "\n")


def agents():
    """hmem.agents: which coding agents (claude / codex) are enabled on this computer."""
    import sys
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from hmem import agents as _a
    return _a
