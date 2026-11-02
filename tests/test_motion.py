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


def test_install_from_the_panel(monkeypatch):
    import time
    monkeypatch.setattr(motion, "_which", lambda c: "/usr/bin/" + c)
    monkeypatch.setattr(motion, "deps_ready", lambda p: (p / "package.json").exists())
    monkeypatch.setattr(motion, "npm_install", lambda p=None, wait=False: {"state": "ready"})
    assert motion.status()["installed"] is False  # nothing there yet: the panel shows Download
    motion.install()
    for _ in range(50):
        if motion.install_state()["state"] != "running":
            break
        time.sleep(0.05)
    s = motion.status()
    assert s["install"]["state"] == "done" and s["installed"] and s["exists"]


def test_films_lists_the_studio_folder(tmp_path):
    assert motion.films() == {"films": []}  # no studio yet
    st = tmp_path / "tabbie" / "motion" / "studio"
    for name, pj in {"old": {"title": "Old", "step": "editor", "sessionId": "abc123", "updatedAt": "2026-01-01T00:00:00Z"},
                     "new": {"title": "New", "updatedAt": "2026-02-01T00:00:00Z"}}.items():
        (st / name).mkdir(parents=True)
        (st / name / "project.json").write_text(json.dumps({"schema": "tabbie.motion-studio/1", "project": "x", **pj}))
    (st / "broken").mkdir()
    (st / "broken" / "project.json").write_text("{")
    (st / "empty").mkdir()
    assert motion.films()["films"] == [
        {"project": "new", "title": "New", "step": "audio", "updatedAt": "2026-02-01T00:00:00Z"},
        {"project": "old", "title": "Old", "step": "editor", "updatedAt": "2026-01-01T00:00:00Z", "sessionId": "abc123"},
    ]


KEY = "sk_" + "a1" * 20


def test_connect_elevenlabs_saves_key_privately(tmp_path):
    p = motion.studio_dir(); p.mkdir(parents=True)
    (p / ".env").write_text("OTHER=1\nELEVENLABS_API_KEY=old\n")
    code, res = motion.set_elevenlabs_key(KEY, check=lambda k: "ok")
    assert code == 200 and res == {"ok": True, "checked": True}
    text = (p / ".env").read_text()
    assert "OTHER=1" in text and f"ELEVENLABS_API_KEY={KEY}" in text and "=old" not in text
    assert oct((p / ".env").stat().st_mode & 0o777) == "0o600"
    assert KEY not in json.dumps(motion.status())  # linked now, key never echoed
    assert motion.status()["elevenlabs"]["linked"] is True


def test_connect_elevenlabs_refuses_bad_keys(tmp_path):
    p = motion.studio_dir(); p.mkdir(parents=True)
    assert motion.set_elevenlabs_key("abc", check=lambda k: "ok")[0] == 400
    assert motion.set_elevenlabs_key(KEY, check=lambda k: "bad")[0] == 400
    assert not (p / ".env").exists()  # nothing written for a rejected key


def test_connect_elevenlabs_offline_still_saves(tmp_path):
    p = motion.studio_dir(); p.mkdir(parents=True)
    code, res = motion.set_elevenlabs_key(KEY, check=lambda k: "unknown")
    assert code == 200 and res["checked"] is False


def test_connect_elevenlabs_needs_the_studio(tmp_path):
    assert motion.set_elevenlabs_key(KEY, check=lambda k: "ok")[0] == 409


def test_hint_never_shows_the_home_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(motion.Path, "home", classmethod(lambda cls: tmp_path))
    assert str(tmp_path) not in motion.status()["elevenlabs"]["hint"]


def test_new_studio_has_the_sfx_library(tmp_path):
    motion.ensure()
    p = motion.studio_dir()
    lib = json.loads((p / "public/audio/sfx/manifest.json").read_text())["sounds"]
    assert len(lib) >= 100 and all((p / "public/audio/sfx" / f).exists() for v in lib.values() for f in v["files"])
    assert (p / "src/audio/Sfx.tsx").exists() and "## Sound effects" in (p / "CLAUDE.md").read_text()
    assert motion.sync_template(p) == []  # already current


def test_older_template_studio_gets_the_additions_once(tmp_path):
    motion.ensure()
    p = motion.studio_dir()
    sfx = p / "public/audio/sfx"
    (sfx / "whoosh_soft_1.mp3").write_bytes(b"mine")  # the user's own take: never overwritten
    (sfx / "pop_soft_1.mp3").unlink()
    man = json.loads((sfx / "manifest.json").read_text())
    man["sounds"] = {"my_sound": {"category": "ui", "files": ["my_sound_1.mp3"], "durations": [1], "prompt": "x"}}
    (sfx / "manifest.json").write_text(json.dumps(man))
    doc = p / "CLAUDE.md"
    doc.write_text(doc.read_text().split("## Sound effects")[0])
    (p / motion.MARKER).write_text("template 1\n")
    added = motion.sync_template(p)
    assert "public/audio/sfx/pop_soft_1.mp3" in added
    assert (sfx / "whoosh_soft_1.mp3").read_bytes() == b"mine"
    sounds = json.loads((sfx / "manifest.json").read_text())["sounds"]
    assert "my_sound" in sounds and "pop_soft" in sounds
    assert doc.read_text().count("## Sound effects") == 1
    assert motion.sync_template(p) == []  # once


def test_custom_studio_is_never_synced(tmp_path):
    p = motion.studio_dir(); p.mkdir(parents=True); (p / "mine.txt").write_text("x")
    assert motion.sync_template(p) == [] and sorted(x.name for x in p.iterdir()) == ["mine.txt"]
