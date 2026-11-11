"""Repo suggestions for New project / folder picker: blind = most recently active unclaimed repos,
with a typed name = ranked by folder / package.json / remote / README title. Bounded, cached scan."""
from __future__ import annotations
import json, os, subprocess, sys, time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    home = (tmp_path / "home").resolve()
    home.mkdir()
    monkeypatch.setenv("HMEM_ROOT", str(tmp_path / "hermes" / "memory"))
    monkeypatch.setenv("HERMES_PROJECT_ROOT", str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HMEM_SKILLS_DIR", str(tmp_path / "skills"))
    from hmem import api, projects, repo_suggest, store
    store._HEAD_CACHE.update(head=None, blobs={})
    projects._CACHE.update(sig=None, all=[])
    repo_suggest._CACHE.update(at=0.0, base=None, repos=[])
    store.init()
    t0 = time.time() - 100 * 86400

    def repo(rel, age_days=0, remote=None, pkg=None, readme=None):
        d = home / rel
        d.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(d)], check=True)
        if remote:
            subprocess.run(["git", "-C", str(d), "remote", "add", "origin", remote], check=True)
        if pkg:
            (d / "package.json").write_text(json.dumps({"name": pkg}), encoding="utf-8")
        if readme:
            (d / "README.md").write_text(f"# {readme}\n\nbody\n", encoding="utf-8")
        t = t0 + (100 - age_days) * 86400
        for p in [d, d / ".git", d / ".git" / "HEAD", d / ".git" / "config"]:
            os.utime(p, (t, t))
        return d
    return type("E", (), dict(home=home, repo=staticmethod(repo), api=api, projects=projects, rs=repo_suggest))


def names(d):
    return [r["name"] for r in d["repos"]]


def test_blind_is_most_recent_first_and_skips_claimed_hidden_and_plain(env):
    env.repo("old-thing", age_days=40)
    env.repo("Developer/fresh-app", age_days=1)
    env.repo("Developer/org/nested-svc", age_days=5)   # depth 2 under a code folder
    env.repo("mid", age_days=10)
    env.repo(".hidden-repo", age_days=0)
    (env.home / "plain").mkdir()
    claimed = env.repo("claimed", age_days=0)
    env.projects.create("Owner", folders=[str(claimed)])
    st, d = env.api.handle("GET", "/fs/repos", {})
    assert st == 200 and d["matched"] is False
    assert names(d) == ["fresh-app", "nested-svc", "mid", "old-thing"]
    assert all("score" not in r and r["display"].startswith("~") for r in d["repos"])


def test_scan_depth_is_bounded(env):
    env.repo("a/b/too-deep")       # depth 3 under home: not scanned
    env.repo("a/two-deep")
    env.repo("Developer/x/y/deep")  # depth 3 under ~/Developer
    assert names(env.rs.suggest("")) == ["two-deep"]


def test_typed_name_ranks_by_folder_package_remote_and_readme(env):
    env.repo("zebra", age_days=0)
    env.repo("acme-web", age_days=30)
    env.repo("site2", age_days=20, pkg="@acme/landing")
    env.repo("svc", age_days=20, remote="https://user:tok3n@github.com/someone/acme-api.git")
    env.repo("docs", age_days=20, readme="Acme handbook")
    d = env.rs.suggest("Acme")
    assert d["matched"] is True
    assert names(d)[0] == "acme-web" and "zebra" not in names(d)
    assert set(names(d)) == {"acme-web", "site2", "svc", "docs"}
    why = {r["name"]: r.get("why") for r in d["repos"]}
    assert why["acme-web"] is None
    assert why["site2"].startswith("package.json") and why["svc"] == "remote: acme-api" and why["docs"].startswith("README")
    assert "tok3n" not in json.dumps(d)


def test_fuzzy_words_and_fallback(env):
    env.repo("gaffswipe-landlords", age_days=3)
    env.repo("gaffswipe-admin", age_days=1)
    env.repo("other", age_days=0)
    assert names(env.rs.suggest("gaff landlord"))[0] == "gaffswipe-landlords"
    assert names(env.rs.suggest("gffswp"))[:2] == ["gaffswipe-admin", "gaffswipe-landlords"]  # subsequence, recency tiebreak
    d = env.rs.suggest("Quantum Teapot")
    assert d["matched"] is False and names(d)[0] == "other"  # nothing similar: recent list instead


def test_scan_is_cached(env, monkeypatch):
    env.repo("one")
    assert names(env.rs.suggest("")) == ["one"]
    env.repo("two")
    assert names(env.rs.suggest("")) == ["one"]  # within the TTL: no rescan per keystroke
    monkeypatch.setattr(env.rs, "TTL", 0.0)
    assert sorted(names(env.rs.suggest(""))) == ["one", "two"]


def test_field_score_basics(env):
    fs = env.rs.field_score
    assert fs("Acme", "acme") == 100
    assert fs("acme", "acme-web") > fs("acme", "my-acme") > fs("acme", "xyz") == 0
    assert fs("", "acme") == 0
    assert env.rs.clean_remote("git@github.com:o/r.git") == "github.com/o/r"


def test_tilde_windows_paths(monkeypatch):
    # Windows: home is C:\Users\Alex, paths use backslashes and compare without case
    from hmem import projects
    from pathlib import Path
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: "C:\\Users\\Alex"))
    monkeypatch.setattr(projects.os.path, "normcase", lambda s: s.replace("/", "\\").lower())
    assert projects.tilde("C:\\Users\\Alex\\acme-app") == "~\\acme-app"
    assert projects.tilde("c:\\users\\alex") == "~"
    assert projects.tilde("D:\\code\\acme") == "D:\\code\\acme"
