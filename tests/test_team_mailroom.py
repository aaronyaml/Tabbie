import datetime as dt
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("cryptography")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "team"))
import mailroom as M  # noqa: E402


class FakeTT:
    def __init__(self, home):
        self.home = home

    def load(self, name, default=None):
        p = self.home / name
        return json.loads(p.read_text()) if p.exists() else default

    def save(self, name, data):
        (self.home / name).write_text(json.dumps(data))


def iso(days_ago):
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago)).isoformat()


def test_triage_runs_once_a_day_from_4pm_uk(tmp_path):
    tt = FakeTT(tmp_path)
    at = lambda h, m=0: dt.datetime(2026, 11, 18, h, m, tzinfo=M.UK)  # noqa: E731
    assert not M.triage_due(tt, at(15, 59))
    assert M.triage_due(tt, at(16, 0))
    s = M.state(tt)
    s["last_triage"] = "2026-11-18"
    M.save_state(tt, s)
    assert not M.triage_due(tt, at(18))
    assert M.triage_due(tt, dt.datetime(2026, 11, 19, 16, 5, tzinfo=M.UK))


def test_triage_notes_drafts_follows_up_and_skips_noted_mail(tmp_path, monkeypatch):
    tt = FakeTT(tmp_path)
    s = M.state(tt)
    s["notes"]["old-note"] = {"verdict": "no_reply", "note": "seen", "at": 1, "thread_id": "t0"}
    M.save_state(tt, s)
    inbox = [
        {"id": "old-note", "thread_id": "t0", "created_at": iso(1), "direction": "in", "subject": "Seen", "from_address": "a@x.com"},
        {"id": "m1", "thread_id": "t1", "created_at": iso(1), "direction": "in", "subject": "Viewing?", "from_address": "jo@agent.co.uk"},
        {"id": "m2", "thread_id": "t2", "created_at": iso(2), "direction": "in", "subject": "Sale now on", "from_address": "news@shop.com"},
        {"id": "m3", "thread_id": "t3", "created_at": iso(3), "direction": "in", "subject": "Fees", "from_address": "sam@lets.co.uk"},
        {"id": "m4", "thread_id": "t4", "created_at": iso(40), "direction": "in", "subject": "Ancient", "from_address": "z@z.com"},
    ]
    threads = {r["id"]: {"thread_id": r["thread_id"], "messages": [r]} for r in inbox}
    calls = []

    def fake_call(_tt, op, **a):
        calls.append((op, a))
        if op == "list":
            return {"mailbox": "aaron@example.com", "name": "Aaron", "messages": inbox}
        if op == "thread":
            assert a.get("peek") is True  # triage never marks mail read
            return threads[a["id"]]
        if op == "standard":
            return {"standard": "be nice"}
        raise AssertionError(op)

    verdicts = iter([
        {"verdict": "reply", "note": "Agent asks about viewings.", "reply": "Hi Jo,\n\nYes.\n\nThanks,"},
        {"verdict": "no_reply", "note": "Marketing."},
        {"verdict": "follow_up", "note": "Went quiet.", "why": "No answer on fees."},
    ])
    monkeypatch.setattr(M, "call", fake_call)
    monkeypatch.setattr(M, "claude", lambda prompt, timeout=240: json.dumps(next(verdicts)))
    done = M.triage(tt, log=lambda *_: None)
    assert done == {"read": 3, "drafts": 1, "followups": 1, "skipped": 1}
    assert not any(op == "send" for op, _ in calls)  # never sends
    assert [a["id"] for op, a in calls if op == "thread"] == ["m1", "m2", "m3"]  # noted + too-old mail untouched
    s = M.state(tt)
    assert s["notes"]["m2"]["verdict"] == "no_reply" and "m4" not in s["notes"]
    d = s["drafts"]["t1"]
    assert d["to"] == ["jo@agent.co.uk"] and d["subject"] == "Re: Viewing?" and d["by"] == "triage"
    assert s["followups"]["t3"]["why"] == "No answer on fees." and "t3" not in s["drafts"]
    rows = M.annotate(tt, inbox)
    assert rows[1]["note"]["verdict"] == "reply" and rows[1]["has_draft"] and rows[3]["followup"]["why"]
    # a second run reads nothing new
    assert M.triage(tt, log=lambda *_: None)["read"] == 0
