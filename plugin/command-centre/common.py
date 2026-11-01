"""Shared paths, config loading and small JSON-state helpers."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

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
    text = path.read_text()
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
    path.write_text(text)


def load_config() -> dict:
    return _load_yaml(CONFIG_DIR / "router.yaml")


def load_projects_file() -> dict:
    return _load_yaml(CONFIG_DIR / "projects.yaml")


def save_projects_file(data: dict) -> None:
    _dump_yaml(CONFIG_DIR / "projects.yaml", data)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: Path, data: Any) -> None:
    """Atomic write so a crash never leaves half a state file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, indent=2, default=str)
    os.replace(tmp, path)


def log_decision(**fields: Any) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    fields.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S"))
    with LOG_FILE.open("a") as fh:
        fh.write(json.dumps(fields, default=str) + "\n")


def agents():
    """hmem.agents: which coding agents (claude / codex) are enabled on this Mac."""
    import sys
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from hmem import agents as _a
    return _a
