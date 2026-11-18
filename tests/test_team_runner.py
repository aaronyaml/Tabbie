"""runner.py: only the owner's own fresh run requests are acted on; chats run here stream back into the shared chat."""
import asyncio
import json
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("cryptography")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "team"))
import mirror  # noqa: E402
import runner  # noqa: E402
import ttcore as C  # noqa: E402


class FakeTT:
    def load(self, name, default=None):
        return default


def make_mirror(tmp_path, msgs=()):
    (tmp_path / "sessions").mkdir(exist_ok=True)
    (tmp_path / "sessions" / "s1.json").write_text(json.dumps({"title": "T", "project_id": "p9", "updated_at": time.time(), "messages": list(msgs)}))
    (tmp_path / "sessions" / "_index.json").write_text(json.dumps([{"session_id": "s1", "title": "T", "project_id": "p9"}]))
    (tmp_path / "projects.json").write_text(json.dumps([{"project_id": "p9", "name": "private"}]))
    mirror.WEBUI, mirror.SESS = tmp_path, tmp_path / "sessions"
    return mirror.Mirror(FakeTT(), "aaron", {"projects": []})


def drain(m):
    out = []
    while not m.outbox.empty():
        out.append(m.outbox.get_nowait())
    return out


def test_bound_chat_streams_into_the_shared_room_without_meta_or_duplicate_prompt(tmp_path):
    m = make_mirror(tmp_path, [{"role": "user", "content": "context…\n\n---\n\nhello there\n\n[Attached files: /x/a.png]", "message_uid": "u1"},
                               {"role": "assistant", "content": "hi", "message_uid": "a1", "finish_reason": "stop"}])
    m.bind("s1", "s-shared01")
    m.expect("s1", "p-abcdefgh12", "hello there")
    m.scan()  # in no shared project, but bound: kept
    assert "s1" in m.sessions
    m.poll(m.sessions["s1"])
    out = drain(m)
    assert all(room == "s-shared01" for room, *_ in out)
    assert not [x for x in out if x[1] == "session.meta"]  # the shared chat's title and owner stay its owner's
    user = [p for _r, t, p, _d in out if t == "message" and p["role"] == "user"][0]
    assert user["text"] == "hello there" and user["prompt"] == "p-abcdefgh12"  # the site shows its own bubble
    assert m.state["sessions"]["s1"]["remote"] == {}


def test_bound_chat_survives_restart(tmp_path):
    m = make_mirror(tmp_path)
    m.bind("s1", "s-shared01")
    m2 = mirror.Mirror(FakeTT(), "aaron", {"projects": []})
    m2.state = m.state
    m2.scan()
    assert m2.sessions["s1"].st["room"] == "s-shared01"


def test_context_keeps_the_latest_and_ends_with_the_prompt():
    hist = [{"role": "user", "by": "aaron", "text": "x" * 5000}, {"role": "assistant", "by": "aaron", "text": "answer"}]
    out = runner.context_of(hist, "Outreach", "aaron", "now this")
    assert "“Outreach”" in out and "Aaron" in out and out.endswith("---\n\nnow this")
    assert "**Tabbie:** answer" in out and "x" * 4001 not in out


class Opened:
    def __init__(self, author, payload, ts=None):
        self.e = {"author": author, "ts": ts or C.now_ms()}
        self.payload = payload


def runner_with(tmp_path, opened):
    m = make_mirror(tmp_path)
    r = runner.Runner(m)
    r.open = lambda e, room, seq: {"payload": opened.payload, "device": "d", "can_execute": False}
    started = []
    r.run = lambda room, pid: started.append((room, pid)) or asyncio.sleep(0)
    return r, started, opened


@pytest.mark.parametrize("author,payload,ts,runs", [
    ("aaron", {"kind": "run", "to": "aaron", "room": "s-abcd1234", "prompt": "p-abcdefgh12"}, None, True),
    ("leo", {"kind": "run", "to": "aaron", "room": "s-abcd1234", "prompt": "p-abcdefgh12"}, None, False),  # not the owner
    ("aaron", {"kind": "run", "to": "leo", "room": "s-abcd1234", "prompt": "p-abcdefgh12"}, None, False),  # for another Mac
    ("aaron", {"kind": "run", "to": "aaron", "room": "../x", "prompt": "p-abcdefgh12"}, None, False),
    ("aaron", {"kind": "run", "to": "aaron", "room": "s-abcd1234", "prompt": "p-abcdefgh12"}, C.now_ms() - 10 * 60_000, False),  # stale
])
def test_only_the_owners_fresh_run_requests(tmp_path, author, payload, ts, runs):
    async def go():
        o = Opened(author, payload, ts)
        r, started, _ = runner_with(tmp_path, o)
        r.on_index({"type": "queue", "body": json.dumps(o.e)}, 5)
        await asyncio.sleep(0)
        return started

    assert bool(asyncio.run(go())) is runs


def test_session_meta_is_remembered_for_titles(tmp_path):
    o = Opened("leo", {"id": "s-abcd1234", "title": "Viewings", "project": "Acme"})
    r, _s, _ = runner_with(tmp_path, o)
    r.on_index({"type": "session.meta", "body": json.dumps(o.e)}, 3)
    assert r.metas["s-abcd1234"] == {"title": "Viewings", "project": "Acme", "owner": "leo"}


def test_webui_password_is_read_from_its_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("HERMES_WEBUI_PASSWORD", raising=False)
    f = tmp_path / ".env"
    f.write_text("OTHER=1\nexport HERMES_WEBUI_PASSWORD='pa ss'\n")
    monkeypatch.setattr(runner, "WEBUI_ENV", f)
    assert runner._password() == "pa ss"


class FakeSidecar:
    def __init__(self):
        self.posted = []

    def get(self, path):
        return {"choice": {"provider": "anthropic", "model": "claude-opus-5-5"}, "catalog": {"providers": {
            "anthropic": {"name": "Claude", "models": [{"id": "claude-opus-5-5", "name": "Opus 5.5"},
                                                      {"id": "claude-sonnet-5-5", "name": "Sonnet 5.5"}]}}}}

    def post(self, path, body):
        self.posted.append((path, body))
        return {"choice": body}


def test_models_are_published_trimmed_and_only_offered_ones_are_applied(tmp_path):
    m = make_mirror(tmp_path)
    m.sidecar = FakeSidecar()
    r = runner.Runner(m)
    cat = r.models()
    assert cat["kind"] == "models" and cat["default"] == {"provider": "anthropic", "model": "claude-opus-5-5"}
    assert [x["name"] for x in cat["providers"][0]["models"]] == ["Opus 5.5", "Sonnet 5.5"]
    assert r.use_model("s1", {"provider": "anthropic", "model": "claude-sonnet-5-5"}) == "Sonnet 5.5"
    assert m.sidecar.posted == [("/executor", {"id": "s1", "provider": "anthropic", "model": "claude-sonnet-5-5", "effort": ""})]
    assert r.use_model("s1", {"provider": "anthropic", "model": "not-a-model"}) is None
    assert r.use_model("s1", "claude") is None and len(m.sidecar.posted) == 1
