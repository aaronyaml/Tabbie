"""bin/link-tools: tools a login shell finds (nvm etc.) are linked where launchd services look."""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="launchd services are macOS-only")


def run(home, *a):
    return subprocess.run([str(ROOT / "bin" / "link-tools"), *a], capture_output=True, text=True, timeout=120,
                          env={"HOME": str(home), "PATH": "/usr/bin:/bin", "SHELL": "/bin/sh"})


def test_a_claude_only_nvm_knows_about_is_linked_and_unlinked_when_gone(tmp_path):
    nvm = tmp_path / ".nvm/versions/node/v22.1.0/bin"
    nvm.mkdir(parents=True)
    (nvm / "claude").write_text("#!/bin/sh\necho fake\n")
    (nvm / "claude").chmod(0o755)
    assert "linked claude" in run(tmp_path).stdout
    link = tmp_path / ".hermes/bin/claude"
    assert link.is_symlink() and link.resolve() == (nvm / "claude").resolve()
    (nvm / "claude").unlink()
    run(tmp_path)
    assert not link.is_symlink()


def test_updates_relink_tools():
    text = (ROOT / "plugin/command-centre/updates.py").read_text(encoding="utf-8")
    assert "_link_tools()" in text and '"link-tools"' in text
