"""Every test gets a throwaway memory store: nothing a test does may reach ~/hermes/memory."""
from __future__ import annotations
import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix="hmem-tests-")
os.environ["HMEM_ROOT"] = os.path.join(_TMP, "memory")  # import-time safety net
os.environ["HMEM_WEBUI_SESSIONS"] = os.path.join(_TMP, "webui", "sessions")  # never the real WebUI chats
os.environ["HMEM_NO_LLM"] = "1"  # never a real `claude -p` call; tests stub the LLM where they need it
os.environ["TABBIE_LIMITS_GATE"] = "off"  # never a real usage-endpoint call (tests pass their own rows)
os.environ["TABBIE_LINKED"] = ""  # no `claude auth` / `codex login` subprocess: nothing linked unless a test says so
os.environ["TABBIE_NO_SERVICES"] = "1"  # never install or reload real LaunchAgents


@pytest.fixture(autouse=True)
def _isolated_memory(tmp_path, monkeypatch):
    monkeypatch.setenv("HMEM_ROOT", str(tmp_path / "memory"))
    monkeypatch.setenv("HMEM_WEBUI_SESSIONS", str(tmp_path / "webui" / "sessions"))
    monkeypatch.setenv("HMEM_NO_LLM", "1")
    import sys
    t = sys.modules.get("hmem.trace")
    if t is not None:
        monkeypatch.setattr(t, "WEBUI_SESSIONS", tmp_path / "webui" / "sessions")


@pytest.fixture(autouse=True)
def _isolated_agents(tmp_path, monkeypatch):
    """config/agents.yaml lives in a temp dir (no file = both agents), never ~/hermes/config."""
    import sys
    from pathlib import Path
    root = str(Path(__file__).resolve().parents[1])
    if root not in sys.path:
        sys.path.insert(0, root)
    from hmem import agents
    monkeypatch.setattr(agents, "path", lambda: tmp_path / "config" / "agents.yaml")
