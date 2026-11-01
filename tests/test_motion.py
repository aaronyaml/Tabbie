"""Motion studio folder (hmem/motion.py): created from the template when missing, never touching an
existing folder, and a status that says what's linked without ever carrying a key."""
from __future__ import annotations

import json

import pytest

from hmem import motion


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("COMMAND_CENTRE_HOME", str(tmp_path / "tabbie"))
    monkeypatch.delenv("TABBIE_MOTION_STUDIO", raising=False)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setattr(motion, "higgsfield_linked", lambda: False)
    monkeypatch.setattr(motion, "npm_install", lambda p=None, wait=False: {"state": "no-node"})


def test_default_is_inside_tabbie(tmp_path):
    assert motion.studio_dir() == tmp_path / "tabbie" / "motion"


def test_config_and_env_override(tmp_path, monkeypatch):
    cfg = tmp_path / "tabbie" / "config"
    cfg.mkdir(parents=True)
    (cfg / "motion.yaml").write_text("# mine\nstudio: ~/films  # my own studio\n")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert motion.studio_dir() == tmp_path / "home" / "films"
    (cfg / "motion.yaml").write_text("studio: studios/a\n")
    assert motion.studio_dir() == tmp_path / "tabbie" / "studios" / "a"  # relative = inside Tabbie
    monkeypatch.setenv("TABBIE_MOTION_STUDIO", str(tmp_path / "x"))
    assert motion.studio_dir() == tmp_path / "x"


def test_ensure_creates_from_template(tmp_path):
    r = motion.ensure()
    p = tmp_path / "tabbie" / "motion"
    assert r == {"path": str(p), "created": True}
    for f in ("package.json", "CLAUDE.md", "MOTION_RULES.md", ".gitignore", ".env.example", motion.MARKER,
              "src/Root.tsx", "scripts/analyze.mjs", "studio/.gitkeep"):
        assert (p / f).exists(), f
    assert not (p / "gitignore").exists() and not (p / "VERSION").exists()
    assert json.loads((p / "package.json").read_text())["dependencies"]["remotion"]
    assert not list(p.parent.glob(".motion.creating-*"))
    assert motion.ensure()["created"] is False  # second time: left alone


def test_ensure_never_touches_an_existing_studio(tmp_path):
    p = tmp_path / "mine"
    p.mkdir()
    (p / "keep.txt").write_text("x")
    assert motion.ensure(p)["created"] is False
    assert sorted(x.name for x in p.iterdir()) == ["keep.txt"]
    empty = tmp_path / "empty"
    empty.mkdir()
    assert motion.ensure(empty)["created"] is True  # an empty folder is filled in


def test_status_creates_and_never_leaks_the_key(tmp_path):
    s = motion.status()
    assert s["exists"] is False and s["elevenlabs"]["linked"] is False  # status alone never creates
    s = motion.status(create=True)
    assert s["created"] and s["exists"] and s["template"]
    assert s["elevenlabs"]["linked"] is False and "ELEVENLABS_API_KEY" in s["elevenlabs"]["hint"]
    assert s["higgsfield"]["linked"] is False and "higgsfield auth login" in s["higgsfield"]["hint"]
    secret = "sk_test_do_not_leak_123"
    (tmp_path / "tabbie" / "motion" / ".env").write_text(f"ELEVENLABS_API_KEY={secret}\n")
    s = motion.status()
    assert s["elevenlabs"]["linked"] is True
    assert secret not in json.dumps(s)


def test_empty_key_is_not_linked(tmp_path):
    motion.ensure()
    (tmp_path / "tabbie" / "motion" / ".env").write_text("ELEVENLABS_API_KEY=\n# ELEVENLABS_API_KEY=x\n")
    assert motion.status()["elevenlabs"]["linked"] is False
