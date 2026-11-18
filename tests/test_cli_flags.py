"""cli_flags: an agent whose --help lacks an option Tabbie passes is reported, once per installed version."""
import sys
from pathlib import Path

import pytest

posix = pytest.mark.skipif(sys.platform == "win32", reason="fake agents are shell scripts")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugin"))


def _mod(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("cli_flags_t", ROOT / "plugin" / "command-centre" / "cli_flags.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    monkeypatch.setattr(m, "CACHE", tmp_path / "cli-flags.json")
    return m


def _fake(tmp_path, monkeypatch, name, help_text):
    b = tmp_path / "bin"
    b.mkdir(exist_ok=True)
    (b / name).write_text(f"#!/bin/sh\ncat <<'X'\n{help_text}\nX\n")
    (b / name).chmod(0o755)
    monkeypatch.setenv("PATH", f"{b}:/usr/bin:/bin")


@posix
def test_reports_missing_options_and_says_how_to_update(tmp_path, monkeypatch):
    m = _mod(tmp_path, monkeypatch)
    need = m.NEEDS["claude"][1]
    _fake(tmp_path, monkeypatch, "claude", "Usage: claude\n" + "\n".join(f"  {f}  x" * 3 for f in need if f != "--effort"))
    assert m.missing("claude") == ["--effort"]
    p = m.problems(("claude",))
    assert len(p) == 1 and "--effort" in p[0] and "claude update" in p[0]
    assert m.missing("codex") is None  # not installed: not a problem here


@posix
def test_a_current_agent_is_fine_and_cached(tmp_path, monkeypatch):
    m = _mod(tmp_path, monkeypatch)
    _fake(tmp_path, monkeypatch, "claude", "Usage: claude\n" + "\n".join(f"  {f}  something" for f in m.NEEDS["claude"][1]) + "\n" + "x" * 300)
    assert m.missing("claude") == [] and m.problems(("claude",)) == []
    assert "claude" in (tmp_path / "cli-flags.json").read_text()


def test_every_option_tabbie_passes_claude_is_checked():
    import re
    src = (ROOT / "plugin/command-centre/providers.py").read_text(encoding="utf-8")
    m = re.search(r'cmd = \["claude", "-p".*?claude_args', src, re.S).group(0)
    used = set(re.findall(r'"(--[a-zA-Z-]+)"', m))
    import importlib.util
    spec = importlib.util.spec_from_file_location("cli_flags_t2", ROOT / "plugin" / "command-centre" / "cli_flags.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert used <= set(mod.NEEDS["claude"][1]), used - set(mod.NEEDS["claude"][1])
