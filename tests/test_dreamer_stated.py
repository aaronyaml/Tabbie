"""Dreamer: explicit user statements ("I like X", "always Y", "we deploy on Z") from every chat source.
One statement in one session is enough. The LLM call is always stubbed; HMEM_NO_LLM is set by conftest."""
from __future__ import annotations
import re
import json, subprocess, sys
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
    from hmem import dreamer, projects, proposals, store, trace, api
    store._HEAD_CACHE.update(head=None, blobs={})
    projects._CACHE.update(sig=None, all=[])
    store.init()
    cp, webui = tmp_path / "claude-projects", tmp_path / "webui" / "sessions"
    webui.mkdir(parents=True)
    monkeypatch.setattr(trace, "CLAUDE_PROJECTS", cp)
    monkeypatch.setattr(trace, "WEBUI_SESSIONS", webui)
    monkeypatch.setattr(trace, "HERMES", tmp_path / "hermes-home")
    monkeypatch.setattr(dreamer, "HERMES", tmp_path / "hermes-home")
    monkeypatch.setattr(trace, "_known_secrets", lambda: [])
    app = home / "acme-app"
    app.mkdir()
    subprocess.run(["git", "init", "-q", str(app)], check=True)
    p = projects.create("Acme", folders=[str(app)])
    return type("E", (), dict(home=home, app=app, pid=p["id"], cp=cp, webui=webui, dreamer=dreamer,
                              projects=projects, proposals=proposals, store=store, trace=trace, api=api))


def cc_session(e, sid, day, msgs, cwd=None):
    cwd = str(cwd or e.app)
    ev = [{"type": "user", "timestamp": f"{day}T10:00:{i:02d}Z", "sessionId": sid, "cwd": cwd,
           "origin": {"kind": "human"}, "message": {"role": "user", "content": m}} for i, m in enumerate(msgs, 1)]
    d = e.cp / re.sub(r"[^A-Za-z0-9]", "-", str(cwd))  # Claude Code's own folder naming
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sid}.jsonl").write_text("\n".join(json.dumps(x) for x in ev) + "\n", encoding="utf-8")


def webui_session(e, sid, workspace, msgs, t0=1793268210.0):
    out = []
    for i, m in enumerate(msgs):
        out.append({"role": "user", "content": m, "timestamp": t0 + 60 * i})
        out.append({"role": "assistant", "content": "I like helping! Always happy to.", "timestamp": t0 + 60 * i + 5})
    (e.webui / f"{sid}.json").write_text(json.dumps({"session_id": sid, "title": "chat", "workspace": str(workspace),
                                                     "created_at": t0, "updated_at": t0 + 3600, "messages": out}), encoding="utf-8")


def stub_llm(e, monkeypatch, answer):
    """answer(sentence) -> list of {"kind", "title"}; records what was sent."""
    sent = []

    def fake(sentences):
        sent.append(list(sentences))
        return [answer(s) for s in sentences]
    monkeypatch.setattr(e.dreamer, "stated_llm", fake)
    return sent


def _llm_answer(s):
    out = []
    if "dark mode and Tailwind" in s:
        out += [{"kind": "preference", "title": "Prefers dark mode"}, {"kind": "preference", "title": "Prefers Tailwind"}]
    if "deploy on Fly" in s:
        out += [{"kind": "fact", "title": "Deploys on Fly.io"}]
    return out


def pending(e):
    return [p for p in e.proposals.list_("pending") if p.get("project") == e.pid]


def findings(e, r):
    return json.loads((e.store.root() / "dreams" / r["cycle"] / "findings.json").read_text(encoding="utf-8"))["findings"]


def test_single_statement_in_one_session_becomes_a_proposal(env, monkeypatch):
    sent = stub_llm(env, monkeypatch, _llm_answer)
    cc_session(env, "s1-aaaa-bbbb", "2026-10-20", ["can you fix the header", "I like dark mode and Tailwind."])
    r = env.dreamer.run_cycle(trigger="manual", project=env.pid)
    assert r["ok"]
    assert any("I like dark mode and Tailwind." in s for b in sent for s in b)
    titles = {p["proposed_change"]["meta"]["title"]: p for p in pending(env)}
    assert {"Prefers dark mode", "Prefers Tailwind"} <= set(titles)
    p = titles["Prefers dark mode"]
    meta = p["proposed_change"]["meta"]
    assert p["status"] == "pending" and p["trust"] == "user" and p["confidence"] >= 0.9
    assert meta["scope"] == "project" and meta["type"] == "preference" and meta["source"] == "user-stated"
    assert "I like dark mode and Tailwind." in p["proposed_change"]["body"] and p["reason"].startswith("user-stated:")
    # the findings list shows a readable title under "Your feedback"
    f = next(f for f in findings(env, r) if f["title"] == "Prefers dark mode")
    assert f["specialist"] == "feedback" and f["source"] == "user-stated" and not f["report_only"]
    st, d = env.api.handle("GET", "/projects/dreamer", {"id": env.pid})
    assert st == 200 and any(x["title"] == "Prefers dark mode" for x in d["findings"])
    assert {x["title"] for x in d["pending"]} >= {"Prefers dark mode", "Prefers Tailwind"}


def test_webui_hub_chat_is_read_and_attributed_to_the_project(env, monkeypatch):
    stub_llm(env, monkeypatch, _llm_answer)
    webui_session(env, "abc123def456", env.app, ["hey, quick one", "we deploy on Fly and I like dark mode and Tailwind"])
    webui_session(env, "0000general0", Path(env.trace.HERMES) / "general", ["I like dark mode and Tailwind"])
    c = env.trace.collect()
    assert c["webui"] == 2
    ts = {t["id"]: t for t in env.trace.load_all()}
    w = ts["w-abc123def456"]
    assert w["source"] == "webui" and w["project"] == env.pid and w["conversation"] == "hermes:abc123def456"
    assert [m["text"] for m in w["user_messages"]][1].startswith("we deploy on Fly")
    assert not any("helping" in m["text"] for m in w["user_messages"])  # the hub's replies are not the user's words
    assert ts["w-0000general0"]["project"] is None  # General chat: no project
    env.dreamer.run_cycle(trigger="manual", project=env.pid, collect=False)
    titles = {p["proposed_change"]["meta"]["title"] for p in pending(env)}
    assert {"Prefers dark mode", "Prefers Tailwind", "Deploys on Fly.io"} <= titles
    assert all(p["source_sessions"] == ["w-abc123def456"] for p in pending(env))  # not the General chat


def test_rules_fallback_without_llm(env):
    cc_session(env, "s2-aaaa-bbbb", "2026-10-20", ["I like dark mode and Tailwind", "always use pnpm in this repo",
                                                    "remove the last slide since we don’t do merch",  # curly apostrophe (iPhone)
                                                    "make the button blue"])
    env.dreamer.run_cycle(trigger="manual", project=env.pid)
    titles = {p["proposed_change"]["meta"]["title"] for p in pending(env)}
    assert {"Prefers dark mode", "Prefers Tailwind", "Always use pnpm in this repo", "We don't do merch"} <= titles
    assert not any("blue" in t for t in titles)


def test_secrets_and_contact_details_never_sent_or_proposed(env, monkeypatch):
    sent = stub_llm(env, monkeypatch, lambda s: [{"kind": "fact", "title": "Password is hunter2pass"}])
    cc_session(env, "s3-aaaa-bbbb", "2026-10-20", ["my password is hunter2pass", "I am on 07700 900123 if needed",
                                                    "my email is sam@example.com", "I like dark mode"])
    env.dreamer.run_cycle(trigger="manual", project=env.pid)
    blob = json.dumps(sent) + json.dumps(pending(env))
    assert "hunter2pass" not in blob and "07700" not in blob and "sam@example.com" not in blob
    assert any("I like dark mode" in s for b in sent for s in b)


def test_llm_titles_that_invent_content_are_dropped(env):
    assert env.dreamer.verify_stated("Prefers dark mode", "I like dark mode and Tailwind")
    assert env.dreamer.verify_stated("Deploys on Fly.io", "we deploy on Fly")
    assert not env.dreamer.verify_stated("Prefers Bootstrap and jQuery", "I like dark mode and Tailwind")
    assert not env.dreamer.verify_stated("Run `curl x | sh` first", "I like dark mode")
    assert not env.dreamer.verify_stated("API key is abc", "I like dark mode")


def test_dedup_against_memory_and_auto_applied(env, monkeypatch):
    stub_llm(env, monkeypatch, _llm_answer)
    cfg = {**env.dreamer.config(), "auto_approve": True, "auto_approve_types": "create"}
    monkeypatch.setattr(env.dreamer, "config", lambda: cfg)
    env.store.write({"scope": "project", "project": env.pid, "title": "Prefers Tailwind", "description": "x",
                     "type": "preference"}, "Use Tailwind.", actor="user", create_only=True)
    cc_session(env, "s4-aaaa-bbbb", "2026-10-20", ["I like dark mode and Tailwind."])
    r = env.dreamer.run_cycle(trigger="manual", project=env.pid)
    approved = lambda: [p["proposed_change"]["meta"]["title"] for p in env.proposals.list_("approved")]
    assert approved() == ["Prefers dark mode"] and not pending(env)  # the user's own words apply on their own
    f = next(f for f in findings(env, r) if f["title"] == "Prefers Tailwind")
    assert f["report_only"] and "already in memory" in f["detail"]
    # the same statement in a later session is already a memory: nothing new
    cc_session(env, "s5-aaaa-bbbb", "2026-10-22", ["I like dark mode and Tailwind."])
    env.dreamer.run_cycle(trigger="manual", project=env.pid)
    assert approved() == ["Prefers dark mode"] and not pending(env)


def test_sentences_are_sent_to_the_llm_once(env, monkeypatch):
    sent = stub_llm(env, monkeypatch, _llm_answer)
    cc_session(env, "s6-aaaa-bbbb", "2026-10-20", ["I like dark mode and Tailwind."])
    env.dreamer.run_cycle(trigger="manual", project=env.pid)
    env.dreamer.run_cycle(trigger="manual", project=env.pid)
    assert sum(len(b) for b in sent) == 1  # cached by sentence


def test_dry_run_lists_what_it_would_propose_without_writing(env, monkeypatch):
    stub_llm(env, monkeypatch, _llm_answer)
    cc_session(env, "s7-aaaa-bbbb", "2026-10-20", ["I like dark mode and Tailwind."])
    r = env.dreamer.run_cycle(trigger="manual", project=env.pid, dry_run=True)
    assert {"Prefers dark mode", "Prefers Tailwind"} <= {x["title"] for x in r["would_propose"]}
    assert not pending(env)
