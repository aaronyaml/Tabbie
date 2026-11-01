"""Every test gets a throwaway memory store: nothing a test does may reach ~/hermes/memory."""
import os
import tempfile

import pytest

os.environ["HMEM_ROOT"] = os.path.join(tempfile.mkdtemp(prefix="hmem-tests-"), "memory")  # import-time safety net


@pytest.fixture(autouse=True)
def _isolated_memory(tmp_path, monkeypatch):
    monkeypatch.setenv("HMEM_ROOT", str(tmp_path / "memory"))


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
