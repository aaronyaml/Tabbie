"""Team projects: the main copy's project (GitHub repos, instructions, memories) is set up on a teammate's Mac
by itself, kept current, and only ever accepted from that teammate's own Mac."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "team"))
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="team projects run on the Mac")


def g(*a, cwd=None):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


class FakeTT:
    def __init__(self, d: Path, trust=None):
        self.d, self.files = d, {"config.json": {"projects": []}, "trust.json": trust or {}}

    def load(self, name, default=None):
        return self.files.get(name, default)

    def save(self, name, data):
        self.files[name] = data

    def config(self):
        return self.files["config.json"]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    home = (tmp_path / "home").resolve()
    home.mkdir()
    monkeypatch.setenv("HMEM_ROOT", str(tmp_path / "hermes" / "memory"))
    monkeypatch.setenv("HERMES_PROJECT_ROOT", str(home))
    monkeypatch.setenv("HOME", str(home))
    from hmem import projects, store
    store._HEAD_CACHE.update(head=None, blobs={})
    projects._CACHE.update(sig=None, all=[])
    store.init()
    import teamprojects as T
    monkeypatch.setattr(T, "BASE", home / "Tabbie Team")
    monkeypatch.setattr(T, "GIT_FLAGS", ["-c", "core.hooksPath=/dev/null"])  # tests clone from local folders
    remotes = tmp_path / "remotes"

    def bare(repo: str, branch="main") -> Path:
        """A local stand-in for github.com/<repo>, with one commit on `branch`."""
        b = remotes / (repo + ".git")
        b.parent.mkdir(parents=True, exist_ok=True)
        g("init", "-q", "--bare", "-b", branch, str(b))
        w = tmp_path / "seed" / repo
        g("clone", "-q", str(b), str(w))
        g("checkout", "-q", "-b", branch, cwd=w)
        (w / "README.md").write_text("hello\n")
        g("add", ".", cwd=w)
        g("commit", "-q", "-m", "first", cwd=w)
        g("push", "-q", "origin", branch, cwd=w)
        return w

    monkeypatch.setattr(T, "clone_urls", lambda repo: [str(remotes / (repo.split("/", 1)[1] + ".git"))])
    return type("E", (), dict(home=home, T=T, P=projects, store=store, bare=staticmethod(bare), tmp=tmp_path))


def _mine(env, name="Acme"):
    """The main copy: two GitHub repos, one folder that isn't on GitHub, and a memory."""
    app, site, local = env.home / "acme-app", env.home / "acme-site", env.home / "acme-notes"
    for d, repo in ((app, "Acme/app"), (site, "Acme/site")):
        g("init", "-q", "-b", "main", str(d))
        g("remote", "add", "origin", f"git@github.com:{repo}.git", cwd=d)
    local.mkdir()
    p = env.P.create(name, description="the app", folders=[str(app), str(site), str(local)], primary_path=str(app),
                     instructions="Ship from production.")
    env.store.write({"scope": "project", "project": p["id"], "title": "Main stays empty", "type": "rule"},
                    "push to production, main stays empty", actor="user")
    return p


def test_recipe_has_github_repos_and_memories_only(env):
    p = _mine(env)
    r = env.T.recipe(env.P.get(p["id"]))
    assert {x["label"]: x["repo"] for x in r["repos"]} == {"acme-app": "github.com/Acme/app", "acme-site": "github.com/Acme/site"}
    assert next(x for x in r["repos"] if x["primary"])["label"] == "acme-app"
    assert r["instructions"] == "Ship from production." and r["description"] == "the app"
    assert [m["title"] for m in r["memories"]] == ["Main stays empty"]
    assert env.T.valid(r) is None


def test_only_shared_projects_go_out_and_received_ones_never_go_back(env):
    p = _mine(env)
    tt = FakeTT(env.tmp)
    assert env.T.publishable(tt) == []  # not shared with the team
    tt.files["config.json"]["projects"] = ["acme"]
    assert [r["name"] for r in env.T.publishable(tt)] == ["Acme"]
    due = env.T.due_recipes(tt)
    env.T.mark_published(tt, due[0])
    assert env.T.due_recipes(tt) == []  # unchanged: not sent again
    tt.files["follow.json"] = {"aaron/acme": {"pid": p["id"]}}
    assert env.T.publishable(tt) == []


@pytest.mark.parametrize("bad", [
    {"repo": "evil.example/a/b", "label": "x"},
    {"repo": "github.com/a/b", "label": "../up"},
    {"repo": "github.com/a/b", "label": ".hidden"},
    {"repo": "github.com/a/b", "label": "x", "branch": "--upload-pack=sh"},
    {"repo": "github.com/a/b", "label": "x", "branch": "a..b"},
])
def test_unsafe_recipes_are_refused(env, bad):
    assert env.T.valid({"kind": "project", "name": "Acme", "repos": [bad]}) is not None


def test_only_the_teammates_own_mac_can_publish(env):
    trust = {"owner": "leo", "macs": {"aaron": {"kid": "MACKID", "sign_pub": "x"}}}
    f = env.T.Follower(FakeTT(env.tmp, trust), "leo")
    r = {"kind": "project", "name": "Acme", "repos": [{"repo": "github.com/Acme/app", "label": "app"}]}
    f.offer({"author": "aaron", "kid": "PHONEKID", "ts": 1}, r)  # aaron's browser: no
    f.offer({"author": "leo", "kid": "x", "ts": 1}, r)  # this Mac's own owner: no
    f.offer({"author": "mallory", "kid": "MACKID", "ts": 1}, r)  # not a pinned Mac: no
    assert f.latest == {}
    f.offer({"author": "aaron", "kid": "MACKID", "ts": 5}, r)
    f.offer({"author": "aaron", "kid": "MACKID", "ts": 3}, dict(r, description="older"))
    assert list(f.latest) == ["aaron/acme"] and f.latest["aaron/acme"][0] == 5


def test_teammate_gets_the_project_set_up_and_kept_current(env):
    seed = env.bare("Acme/app", branch="production")
    env.bare("Acme/site")
    recipe = {"kind": "project", "v": 1, "name": "Acme", "description": "the app", "instructions": "Ship from production.",
              "colours": [], "memories": [{"name": "main-stays-empty", "title": "Main stays empty", "type": "rule",
                                           "body": "push to production, main stays empty", "tags": []}],
              "repos": [{"label": "acme-app", "repo": "github.com/Acme/app", "branch": "production", "primary": True},
                        {"label": "acme-site", "repo": "github.com/Acme/site", "branch": "", "primary": False}]}
    tt = FakeTT(env.tmp, {"owner": "leo"})
    f = env.T.Follower(tt, "leo")
    st = f.apply("aaron/acme", "aaron", recipe)
    assert st["errors"] == {} and set(st["paths"]) == {"acme-app", "acme-site"}
    app = Path(st["paths"]["acme-app"])
    assert app == (env.home / "Tabbie Team" / "Acme" / "acme-app").resolve()
    assert g("rev-parse", "--abbrev-ref", "HEAD", cwd=app) == "production"

    p = env.P.get(st["pid"])
    assert p["name"] == "Acme" and env.P.primary(p)["path"] == str(app)
    assert "aaron/" not in p["instructions"] and "leo/<short-topic>" in p["instructions"]
    assert p["instructions"].endswith("Ship from production.")
    assert tt.config()["projects"] == ["Acme"]  # leo's chats in it show on the team site
    mem = env.store.read(f"projects/{p['id']}/main-stays-empty")
    assert mem.meta["team_source"] == "aaron:main-stays-empty" and mem.meta["trust"] == "agent"
    assert env.store.is_committed(mem)
    assert env.T.publishable(tt) == []  # received: never published back

    # aaron pushes; leo's clean clone fast-forwards
    (seed / "new.txt").write_text("x\n")
    g("add", ".", cwd=seed)
    g("commit", "-q", "-m", "more", cwd=seed)
    g("push", "-q", "origin", "production", cwd=seed)
    assert f.sync_all()["aaron/acme/acme-app"] == "current"
    assert (app / "new.txt").exists()
    # local work is never touched
    (app / "README.md").write_text("leo's edit\n")
    assert f.sync_all()["aaron/acme/acme-app"] == "local changes"

    # same recipe again: nothing to do; memory removed on aaron's side: archived here
    assert f.apply("aaron/acme", "aaron", recipe)["pid"] == p["id"]
    f.apply("aaron/acme", "aaron", dict(recipe, memories=[]))
    assert env.store.read(f"projects/{p['id']}/main-stays-empty") is None


def test_missing_github_access_is_reported_not_fatal(env):
    env.bare("Acme/app")
    recipe = {"kind": "project", "name": "Acme", "repos": [
        {"label": "acme-app", "repo": "github.com/Acme/app", "primary": True},
        {"label": "acme-secret", "repo": "github.com/Acme/secret"}]}
    st = env.T.Follower(FakeTT(env.tmp, {"owner": "leo"}), "leo").apply("aaron/acme", "aaron", recipe)
    assert list(st["paths"]) == ["acme-app"] and "acme-secret" in st["errors"]
    assert st["hash"] == ""  # tried again next time
    assert env.P.get(st["pid"])["name"] == "Acme"


def test_an_existing_clone_on_this_mac_is_used(env):
    env.bare("Acme/app")
    mine = env.home / "acme"
    g("clone", "-q", str(env.tmp / "remotes" / "Acme" / "app.git"), str(mine))
    g("remote", "set-url", "origin", "https://github.com/Acme/app.git", cwd=mine)
    st = env.T.Follower(FakeTT(env.tmp, {"owner": "leo"}), "leo").apply(
        "aaron/acme", "aaron", {"kind": "project", "name": "Acme", "repos": [{"label": "acme-app", "repo": "github.com/Acme/app"}]})
    assert st["paths"] == {"acme-app": str(mine.resolve())}


def test_team_actor_writes_project_memories_only():
    from hmem import store
    assert store.can_write("team:aaron", "project", "acme-1234")
    assert not store.can_write("team:aaron", "global")


def test_a_clone_in_another_of_my_projects_is_left_alone(env):
    env.bare("Acme/app")
    mine = env.home / "acme"
    g("clone", "-q", str(env.tmp / "remotes" / "Acme" / "app.git"), str(mine))
    g("remote", "set-url", "origin", "https://github.com/Acme/app.git", cwd=mine)
    env.P.create("Leo's own thing", folders=[str(mine)])
    st = env.T.Follower(FakeTT(env.tmp, {"owner": "leo"}), "leo").apply(
        "aaron/acme", "aaron", {"kind": "project", "name": "Acme", "repos": [{"label": "acme-app", "repo": "github.com/Acme/app"}]})
    assert st["errors"] == {} and st["paths"]["acme-app"] != str(mine.resolve())
    assert env.P.get(st["pid"])["name"] == "Acme"
