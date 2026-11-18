"""mirror.py: which messages go out, live drafts, and nothing lost before the relay takes it."""
import json
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("cryptography")  # bin/tt brings it via uv; the plain test run may not have it
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "team"))
import mirror  # noqa: E402


class FakeTT:
    def load(self, name, default=None):
        return default


def make(tmp_path, msgs):
    (tmp_path / "sessions").mkdir()
    (tmp_path / "sessions" / "s1.json").write_text(json.dumps({"title": "T", "project_id": "p1", "updated_at": time.time(), "messages": msgs}))
    (tmp_path / "projects.json").write_text(json.dumps([{"project_id": "p1", "name": "demo"}]))
    mirror.WEBUI, mirror.SESS = tmp_path, tmp_path / "sessions"
    m = mirror.Mirror(FakeTT(), "aaron", {"projects": ["Demo"]})
    s = mirror.Session("s1", {"room": "s-test", "sent": [], "narr": []})
    return m, s


def drain(m):
    out = []
    while not m.outbox.empty():
        out.append(m.outbox.get_nowait())
    return out


def test_messages_once_and_only_marked_sent_when_delivered(tmp_path):
    m, s = make(tmp_path, [{"role": "user", "content": "hi", "message_uid": "u1"}, {"role": "tool", "content": "x"},
                           {"role": "assistant", "content": [{"type": "text", "text": "hello"}], "message_uid": "a1", "finish_reason": "stop"}])
    m.poll(s)
    sent = [x for x in drain(m) if x[1] == "message"]
    assert [x[2]["id"] for x in sent] == ["u1", "a1"] and sent[0][2]["by"] == "aaron"
    assert s.st["sent"] == []  # not delivered yet
    s.mtime = 0
    m.poll(s)
    assert not [x for x in drain(m) if x[1] == "message"]  # queued, so not repeated
    for x in sent:
        x[3]()
    assert s.st["sent"] == ["u1", "a1"]


def test_live_draft_appends_then_resets(tmp_path):
    m, s = make(tmp_path, [])
    m.stream(s, "d1", "Hel")
    m.stream(s, "d1", "Hello")
    m.stream(s, "d1", "Bye")
    d = [x[2] for x in drain(m)]
    assert d == [{"id": "d1", "from": 0, "add": "Hel"}, {"id": "d1", "from": 3, "add": "lo"}, {"id": "d1", "from": 0, "add": "Bye", "reset": True}]


def test_final_reply_takes_over_live_bubble(tmp_path):
    m, s = make(tmp_path, [{"role": "assistant", "content": "done", "message_uid": "a1", "finish_reason": "stop"}])
    m.stream(s, "d1", "do")
    drain(m)
    m.poll(s)
    msg = [x[2] for x in drain(m) if x[1] == "message"][0]
    assert msg["run"] == "d1"


def test_files_split_out_of_text(tmp_path):
    t, paths = mirror.split_files({"attachments": ["a.png"]}, "look at this\n\n[Attached files: /x/a.png, /x/b.md]")
    assert t == "look at this" and paths == ["/x/a.png", "/x/b.md"]
    t, paths = mirror.split_files({}, "Done.\n\nMEDIA:/x/replies.md\n\nNext steps")
    assert t == "Done.\n\nNext steps" and paths == ["/x/replies.md"]
    t, paths = mirror.split_files({"attachments": [{"path": "/x/s.mp3", "name": "s.mp3"}]}, "I've uploaded 1 file(s): /x/s.mp3")
    assert t == "" and paths == ["/x/s.mp3"]


def test_attached_files_are_shared_but_never_secrets(tmp_path, monkeypatch):
    pic = tmp_path / "work" / "post.png"
    pic.parent.mkdir()
    pic.write_bytes(b"\x89PNG" + b"x" * 300_000)
    ok = mirror.file_info(str(pic))  # anywhere on the Mac, not just Tabbie's attachments folder
    assert ok["fid"] and ok["mime"] == "image/png" and ok["size"] == 300_004
    for name in (".env", ".env.local", "id_ed25519", "server.pem", "cert.p12"):
        f = tmp_path / name
        f.write_text("nope")
        assert mirror.file_info(str(f))["local"], name
    ssh = tmp_path / ".ssh"
    ssh.mkdir()
    (ssh / "config").write_text("nope")
    monkeypatch.setattr(mirror, "SECRET_DIRS", [ssh])
    assert mirror.file_info(str(ssh / "config"))["local"]
    assert mirror.file_info(str(tmp_path / "missing.png"))["local"]


def test_file_goes_in_chunks_once(tmp_path, monkeypatch):
    m, s = make(tmp_path, [])
    att = tmp_path / "attachments"
    att.mkdir()
    (att / "f.bin").write_bytes(b"a" * (mirror.CHUNK * 2 + 5))
    monkeypatch.setattr(mirror, "ATTACH", att)
    f = mirror.file_info(str(att / "f.bin"))
    m.send_file(s, f)
    m.send_file(s, f)  # already on its way
    parts = drain(m)
    assert [p[2]["i"] for p in parts] == [0, 1, 2] and all(p[1] == "file" for p in parts)
    parts[-1][3]()
    m.send_file(s, f)
    assert not drain(m) and s.st["files"] == [f["fid"]]


def test_motion_chats_stay_private(tmp_path):
    root = tmp_path / "motion"
    root.mkdir()
    assert mirror.is_motion({"workspace": str(root / "studio")}, root.resolve())
    assert mirror.is_motion({"messages": [{"role": "user", "content": "/motion make an ad"}]}, None)
    assert not mirror.is_motion({"workspace": str(tmp_path / "site"), "messages": [{"role": "user", "content": "hi"}]}, root.resolve())


def test_unshared_session_is_taken_off_the_list(tmp_path):
    m, s = make(tmp_path, [{"role": "user", "content": "hi", "message_uid": "u1"}])
    (tmp_path / "sessions" / "_index.json").write_text(json.dumps([{"session_id": "s1", "project_id": "p1"}]))
    m.scan()
    assert "s1" in m.sessions
    drain(m)
    m.cfg = {"projects": []}
    m.sessions.clear()
    m.scan()
    out = drain(m)
    assert out[0][0] == "index" and out[0][2]["removed"] and "s1" not in m.state["sessions"]


def test_fork_meta_links_a_shared_parent_and_marks_the_fork_point(tmp_path):
    import json as _j, time as _t
    (tmp_path / "sessions").mkdir()
    base = [{"role": "user", "content": "hi", "message_uid": "u1"}, {"role": "assistant", "content": "yo", "message_uid": "a1"}]
    (tmp_path / "sessions" / "p1.json").write_text(_j.dumps({"title": "Parent", "project_id": "g", "updated_at": _t.time(),
        "messages": base + [{"role": "user", "content": "parent only", "message_uid": "u2"}]}))
    (tmp_path / "sessions" / "f1.json").write_text(_j.dumps({"title": "Fork", "project_id": "g", "updated_at": _t.time(), "parent_session_id": "p1",
        "messages": base + [{"role": "user", "content": "fork only", "message_uid": "u3"}]}))
    (tmp_path / "sessions" / "_index.json").write_text(_j.dumps([{"session_id": "p1", "project_id": "g"}, {"session_id": "f1", "project_id": "g"}]))
    (tmp_path / "projects.json").write_text(_j.dumps([{"project_id": "g", "name": "G"}]))
    mirror.WEBUI, mirror.SESS = tmp_path, tmp_path / "sessions"
    m = mirror.Mirror(type("T", (), {"load": lambda self, n, d=None: d})(), "aaron", {"projects": ["G"]})
    m.scan()
    for s in m.sessions.values():
        m.poll(s)
    metas = {}
    while not m.outbox.empty():
        room, t, p, _ = m.outbox.get_nowait()
        if t == "session.meta":
            metas[p["title"]] = p
    assert metas["Fork"]["parent"] == metas["Parent"]["id"] and metas["Fork"]["forkAt"] == "a1"
    assert "parent" not in metas["Parent"]
