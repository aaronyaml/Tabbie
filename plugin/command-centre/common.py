"""Shared paths, config loading and small JSON-state helpers."""

from __future__ import annotations

import contextlib
import json
import os
import sys
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


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def hardware() -> dict:
    """This Mac's memory (GB) and CPU cores, for defaults that should fit it."""
    cores = os.cpu_count() or 4
    gb = 16.0
    try:
        if sys.platform == "darwin":
            import subprocess
            gb = int(subprocess.run(["/usr/sbin/sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5).stdout) / 2**30
        elif hasattr(os, "sysconf") and "SC_PHYS_PAGES" in os.sysconf_names:
            gb = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30
        elif sys.platform == "win32":
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("l", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong), ("rest", ctypes.c_ulonglong * 6)]
            m = MS()
            m.l = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))  # type: ignore[attr-defined]
            gb = m.total / 2**30
    except Exception:  # noqa: BLE001
        pass
    return {"gb": round(gb, 1), "cores": cores}


def fit_parallel(wanted: int, hw: dict) -> int:
    """Each Claude Code job is a Node process (about 0.5-1 GB with its tools). Shared defaults assume a big Mac;
    this keeps an 8 GB laptop from swapping: about one job per 2 GB, never more than the cores, at least 2."""
    return max(2, min(int(wanted), int(hw["gb"] // 2), int(hw["cores"]))) if int(wanted) > 2 else int(wanted)


_HW: dict = {}


def load_config() -> dict:
    """config/router.yaml (shared, in git), then config/router.local.yaml (this Mac only, git-ignored) on top.
    executor.max_parallel from the shared file is fitted to this Mac's memory; set it in router.local.yaml to
    choose it exactly."""
    cfg = _load_yaml(CONFIG_DIR / "router.yaml")
    local_path = CONFIG_DIR / "router.local.yaml"
    local = _load_yaml(local_path) if local_path.exists() else {}
    if isinstance(local, dict) and local:
        cfg = _merge(cfg if isinstance(cfg, dict) else {}, local)
    ex = cfg.get("executor") if isinstance(cfg, dict) else None
    if isinstance(ex, dict) and "max_parallel" in ex and "max_parallel" not in ((local or {}).get("executor") or {}):
        if not _HW:
            _HW.update(hardware())
        try:
            ex["max_parallel"] = fit_parallel(int(ex["max_parallel"]), _HW)
        except (TypeError, ValueError):
            pass
    return cfg


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
