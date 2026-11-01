"""Every test gets a throwaway memory store: nothing a test does may reach ~/hermes/memory."""
import os
import tempfile

import pytest

os.environ["HMEM_ROOT"] = os.path.join(tempfile.mkdtemp(prefix="hmem-tests-"), "memory")  # import-time safety net


@pytest.fixture(autouse=True)
def _isolated_memory(tmp_path, monkeypatch):
    monkeypatch.setenv("HMEM_ROOT", str(tmp_path / "memory"))
