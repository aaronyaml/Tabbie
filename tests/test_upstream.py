"""config/upstream.lock pins the WebUI and Hermes Agent versions Tabbie was tested with; the helper checks
the upstream parts Tabbie relies on are still there."""
from __future__ import annotations

import sqlite3
import subprocess
from importlib import import_module
from pathlib import Path

from test_updates import up  # noqa: F401  (loads the command_centre package)

us = import_module("command_centre.upstream")
ROOT = Path(__file__).resolve().parents[1]


def _git(*a, cwd):
    return subprocess.run(["git", "-C", str(cwd), *a], check=True, capture_output=True, text=True).stdout.strip()


def test_lock_names_both_upstreams():
    lk = us.lock()
    assert set(lk) == {"webui", "agent"} and all(len(v) == 40 for v in lk.values())


def test_relation_never_suggests_going_back(tmp_path):
    r = tmp_path / "r"
    r.mkdir()
    _git("init", "-q", "-b", "main", cwd=r); _git("config", "user.email", "t@t", cwd=r); _git("config", "user.name", "t", cwd=r)
    (r / "a").write_text("1"); _git("add", ".", cwd=r); _git("commit", "-qm", "1", cwd=r)
    one = _git("rev-parse", "HEAD", cwd=r)
    (r / "a").write_text("2"); _git("commit", "-qam", "2", cwd=r)
    two = _git("rev-parse", "HEAD", cwd=r)
    assert us.relation(r, two) == "same"
    assert us.relation(r, one) == "ahead"  # installed is newer: left alone
    _git("checkout", "-q", one, cwd=r)
    assert us.relation(r, two) == "behind"
    assert us.relation(tmp_path / "nope", two) == "missing"


def test_contract_spots_a_changed_database(tmp_path, monkeypatch):
    agent = tmp_path / "agent"
    for _, rel, text in us.AGENT_NEEDS:
        (agent / rel).parent.mkdir(parents=True, exist_ok=True)
        p = agent / rel
        p.write_text((p.read_text() if p.exists() else "") + text + "\n")
    home = tmp_path / "home"
    home.mkdir()
    db = sqlite3.connect(home / "state.db")
    for t, cols in us.DB_NEEDS.items():
        db.execute(f"CREATE TABLE {t} ({', '.join(sorted(cols))})")
    db.commit(); db.close()
    monkeypatch.setenv("HERMES_AGENT_DIR", str(agent))
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(us, "dirs", lambda: {"webui": tmp_path / "no-webui", "agent": agent})
    assert us.contract() == []
    db = sqlite3.connect(home / "state.db")
    db.execute("ALTER TABLE session_model_usage RENAME COLUMN input_tokens TO prompt_tokens")
    db.commit(); db.close()
    (agent / "tools" / "registry.py").write_text("# rewritten\n")
    probs = us.contract()
    assert any("input_tokens" in p for p in probs) and any("registry.py" in p for p in probs)


def test_setup_installs_the_tested_versions():
    assert "--commit" in (ROOT / "bin" / "install-tool").read_text() and "upstream.lock" in (ROOT / "bin" / "install-tool").read_text()
    assert "bin/upstream sync" in (ROOT / "bin" / "setup-terminal").read_text()
    win = (ROOT / "setup" / "windows.py").read_text()
    assert "-Commit" in win and '_pin("webui")' in win


def test_patch_and_lock_changes_restart_the_webui():
    assert "webui" in up._needs(["webui-patches/0004-x.patch"])
    assert "webui" in up._needs(["config/upstream.lock"])
