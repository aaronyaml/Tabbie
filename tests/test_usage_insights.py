"""Insights usage: Claude Code + Codex transcripts -> tokens and API-equivalent cost per period."""
from __future__ import annotations
import datetime as dt
import importlib.util, json, os, sys
from importlib import import_module
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1] / "plugin" / "command-centre"
if "command_centre" not in sys.modules:
    spec = importlib.util.spec_from_file_location("command_centre", PKG / "__init__.py",
                                                  submodule_search_locations=[str(PKG)])
    _m = importlib.util.module_from_spec(spec); sys.modules["command_centre"] = _m
    spec.loader.exec_module(_m)
ui = import_module("command_centre.usage_insights")

TODAY = dt.date.today()


def ts(days_ago: int = 0) -> str:
    """Noon local time, days_ago, as the UTC ISO string the CLIs write."""
    d = dt.datetime.combine(TODAY - dt.timedelta(days=days_ago), dt.time(12)).astimezone()
    return d.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def cc_line(mid, model="claude-opus-5-5", days_ago=0, inp=10, out=100, cr=1000, cw5=0, cw1=0, sid="s1", cwd="/x/proj"):
    return json.dumps({"type": "assistant", "timestamp": ts(days_ago), "sessionId": sid, "cwd": cwd,
                       "message": {"id": mid, "model": model, "usage": {
                           "input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": cr,
                           "cache_creation_input_tokens": cw5 + cw1,
                           "cache_creation": {"ephemeral_5m_input_tokens": cw5, "ephemeral_1h_input_tokens": cw1}}}})


def cx_lines(model="gpt-6.1-sol", sid="t1", cwd="/x/other", totals=((1000, 800, 50),), days_ago=0):
    out = [json.dumps({"type": "session_meta", "timestamp": ts(days_ago), "payload": {"id": sid, "cwd": cwd}}),
           json.dumps({"type": "turn_context", "timestamp": ts(days_ago), "payload": {"model": model}})]
    for i, c, o in totals:
        out.append(json.dumps({"type": "event_msg", "timestamp": ts(days_ago), "payload": {"type": "token_count", "info": {
            "total_token_usage": {"input_tokens": i, "cached_input_tokens": c, "output_tokens": o}}}}))
    return out


def write(p: Path, lines):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(x + "\n" for x in lines))


# --- pricing -----------------------------------------------------------------------------------------------

def test_cost_prices_each_token_kind_separately():
    t = {"in": 1_000_000, "out": 1_000_000, "cr": 1_000_000, "cw5": 1_000_000, "cw1": 1_000_000}
    assert ui.cost_of("claude-opus-5-5", t) == pytest.approx(4 + 20 + 0.20 + 5 + 8)
    assert ui.cost_of("claude-fable-5-2", t) == pytest.approx(8 + 40 + 0.20 + 10 + 16)
    assert ui.cost_of("claude-sonnet-5-5", {"out": 2_000_000, "cr": 1_000_000}) == pytest.approx(20 + 0.10)
    assert ui.cost_of("claude-opus-4-8", {"cr": 1_000_000}) == pytest.approx(0.5)
    assert ui.cost_of("gpt-6.1-sol", {"in": 1_000_000, "out": 1_000_000, "cr": 1_000_000}) == pytest.approx(2 + 10 + 0.10)


def test_long_context_rates_apply_past_the_threshold():
    assert not ui.is_long("claude-haiku-5-5", 100_000) and ui.is_long("claude-haiku-5-5", 100_001)
    assert ui.is_long("gpt-6.1-sol", 300_000) and not ui.is_long("claude-opus-5-5", 900_000)
    assert ui.cost_of("claude-haiku-5-5", {"in": 1_000_000}, long=True) == pytest.approx(0.50)
    assert ui.cost_of("gpt-6.1-sol", {"out": 1_000_000}, long=True) == pytest.approx(15)
    assert ui.cost_of("claude-opus-5-5", {"in": 1_000_000}, long=True) == pytest.approx(4)  # no long tier


def test_rate_lookup_by_prefix_family_fallback_and_unknown():
    assert ui.rate_key("claude-opus-5-5") == ("claude-opus-5-5", False)
    assert ui.rate_key("claude-haiku-4-5-20251001") == ("claude-haiku-4-5", False)   # dated id
    assert ui.rate_key("gpt-5.4-mini") == ("gpt-5.4-mini", False)                    # longest key, not gpt-5.4
    assert ui.rate_key("gpt-5") == ("gpt-5", False)
    assert ui.rate_key("claude-opus-5-7") == ("claude-opus-5-5", True)               # newer: newest listed, estimate
    assert ui.rate_key("gpt-6.2-sol") == ("gpt-6.1-sol", True)
    assert ui.rate_key("gpt-6.1-sol") == ("gpt-6.1-sol", False)
    assert ui.price_for("mystery-model") == (None, False)
    assert ui.cost_of("mystery-model", {"in": 5}) is None


def test_every_price_row_is_complete_and_anthropic_cache_writes_follow_multipliers():
    for k, row in list(ui.RATES.items()) + [(k, v[1]) for k, v in ui.LONG.items()]:
        assert len(row) == 5 and all(v >= 0 for v in row), k
        if k.startswith("claude-"):
            assert row[3] == pytest.approx(row[0] * 1.25) and row[4] == pytest.approx(row[0] * 2), k
    for k in ui.LONG:
        assert k in ui.RATES


def test_newest_models_are_priced():
    for m in ("claude-fable-5-2", "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "gpt-6.1-sol",
              "gpt-6-astra", "gpt-6-luna", "gpt-6.1-terra"):
        assert ui.price_for(m) [0] is not None and ui.price_for(m)[1] is False, m


def test_friendly_names():
    assert ui.friendly("claude-opus-5-5") == "Opus 5.5"
    assert ui.friendly("claude-fable-5-2") == "Fable 5.2"
    assert ui.friendly("gpt-6.1-sol") == "GPT-6.1 Sol"
    assert ui.friendly("claude-opus-5") == "Opus 5"
    assert ui.friendly("gpt-5.4-mini") == "GPT-5.4 Mini"


# --- parsing -----------------------------------------------------------------------------------------------

def test_claude_message_split_over_lines_counts_once_and_skips_synthetic():
    e = {}
    lines = [cc_line("m1", cw1=500).encode(), cc_line("m1", cw1=500).encode(),
             json.dumps({"type": "assistant", "timestamp": ts(), "message": {"id": "m2", "model": "<synthetic>",
                                                                             "usage": {"output_tokens": 0}}}).encode(),
             b"not json {\"usage\"", b'{"type":"user","cwd":"/x/proj","sessionId":"s1"}']
    ui.parse_claude_lines(lines, e)
    assert list(e["msgs"]) == ["m1"]
    day, model, i, o, cr, cw5, cw1, hour = e["msgs"]["m1"]
    assert (model, i, o, cr, cw5, cw1, hour) == ("claude-opus-5-5", 10, 100, 1000, 0, 500, 12)
    assert e["cwd"] == "/x/proj" and e["sid"] == "s1"


def test_cache_write_without_split_counts_as_5m():
    e = {}
    line = json.loads(cc_line("m1"))
    line["message"]["usage"] = {"input_tokens": 1, "output_tokens": 1, "cache_creation_input_tokens": 300}
    ui.parse_claude_lines([json.dumps(line).encode()], e)
    assert e["msgs"]["m1"][5:7] == [300, 0]


def test_codex_uses_deltas_of_running_totals():
    e = {}
    lines = cx_lines(totals=((1000, 800, 50), (1000, 800, 50), (3000, 2500, 120)))
    ui.parse_codex_lines([x.encode() for x in lines], e)
    (key, row), = e["rows"].items()
    assert key.endswith("|12|gpt-6.1-sol|0")
    assert row == [3000 - 2500, 120, 2500]  # uncached in, out, cached in; the repeated event added nothing
    assert e["sid"] == "t1" and e["cwd"] == "/x/other"


# --- scanning + aggregation ---------------------------------------------------------------------------------

def test_incremental_scan_reads_only_new_lines(tmp_path):
    cdir, xdir = tmp_path / "claude", tmp_path / "codex"
    f = cdir / "-x-proj" / "s1.jsonl"
    write(f, [cc_line("m1")])
    write(xdir / "2026" / "r.jsonl", cx_lines())
    files = ui.scan(cdir, xdir, files={})
    e = files[str(f)]
    off1 = e["off"]
    assert off1 == f.stat().st_size and list(e["msgs"]) == ["m1"]
    with open(f, "a") as fh:
        fh.write(cc_line("m2") + "\n" + cc_line("m3")[:20])  # last line still being written
    os.utime(f, (f.stat().st_atime, f.stat().st_mtime + 5))
    files = ui.scan(cdir, xdir, files=files)
    assert sorted(e["msgs"]) == ["m1", "m2"] and e["off"] > off1
    f.write_text(cc_line("z9") + "\n")  # rewritten shorter: start over
    files = ui.scan(cdir, xdir, files=files)
    assert list(files[str(f)]["msgs"]) == ["z9"]
    f.unlink()
    assert str(f) not in ui.scan(cdir, xdir, files=files)


def test_old_files_are_skipped(tmp_path):
    cdir = tmp_path / "claude"
    f = cdir / "p" / "old.jsonl"
    write(f, [cc_line("m1", days_ago=120)])
    old = (dt.datetime.now() - dt.timedelta(days=120)).timestamp()
    os.utime(f, (old, old))
    assert ui.scan(cdir, tmp_path / "none", files={}) == {}


def test_aggregate_windows_models_projects_chats(tmp_path):
    cdir, xdir = tmp_path / "claude", tmp_path / "codex"
    write(cdir / "a" / "s1.jsonl", [cc_line("m1", days_ago=0, sid="s1", cwd="/x/alpha"),
                                     cc_line("m2", days_ago=3, sid="s1", cwd="/x/alpha"),
                                     cc_line("m3", days_ago=20, sid="s1", cwd="/x/alpha", model="claude-sonnet-5-5")])
    # a resumed session copies m1 into a new file: still one message
    write(cdir / "a" / "s2.jsonl", [cc_line("m1", sid="s2", cwd="/x/beta"), cc_line("m4", sid="s2", cwd="/x/beta")])
    write(xdir / "r.jsonl", cx_lines(sid="t1", cwd="/x/alpha", totals=((1_000_000, 0, 0),)))
    files = ui.scan(cdir, xdir, files={})
    r = ui.aggregate(files, chats={"s1": "Fix the login page"}, today=TODAY)
    w = r["windows"]
    assert set(w) == {"today", "7d", "30d", "90d"}
    one = {"in": 10, "out": 100, "cr": 1000, "cw5": 0, "cw1": 0}
    opus = ui.cost_of("claude-opus-5-5", one)
    codex = ui.cost_of("gpt-6.1-sol", {"in": 1_000_000}, long=True)  # one 1M-token prompt: long-context rate
    assert w["today"]["cost"] == pytest.approx(2 * opus + codex, abs=1e-4)  # m1 once + m4 + codex
    assert w["7d"]["cost"] == pytest.approx(3 * opus + codex, abs=1e-4)
    assert w["30d"]["cost"] == pytest.approx(3 * opus + codex + ui.cost_of("claude-sonnet-5-5", one), abs=1e-4)
    assert w["30d"]["tokens"] == 4 * 1110 + 1_000_000
    assert [m["name"] for m in w["30d"]["by_model"]] == ["GPT-6.1 Sol", "Opus 5.5", "Sonnet 5.5"]
    assert w["30d"]["by_model"][0]["estimate"] is False and w["30d"]["by_model"][0]["priced"] is True
    assert w["30d"]["estimated_cost"] == 0
    chats = {c["name"] for c in w["30d"]["by_chat"]}
    assert "Fix the login page" in chats and "beta · Claude Code, outside Tabbie" in chats
    assert {p["name"] for p in w["30d"]["by_project"]} == {"alpha", "beta"}
    assert set(w["today"]["by_source"]) == {"claude", "codex"}
    assert len(r["by_day"]) == 90 and r["by_day"][-1]["day"] == TODAY.strftime("%Y-%m-%d")
    assert r["by_day"][-1]["cost"] == pytest.approx(w["today"]["cost"], abs=1e-3)
    json.dumps(r)  # the sidecar sends it as JSON


def test_chat_map_links_executor_session_to_chat_title(tmp_path):
    rd, sd = tmp_path / "requests", tmp_path / "sessions"
    rid = TODAY.strftime("%Y%m%d") + "-120000-abcdef"
    (rd / rid).mkdir(parents=True)
    (rd / rid / "request.json").write_text(json.dumps({"session_id": "web1", "executor": {"session_id": "cc-uuid"}}))
    sd.mkdir()
    (sd / "web1.json").write_text(json.dumps({"title": "Ship the thing"}))
    ui._chat_mem.update(dirs={}, titles={})
    assert ui.chat_map(rd, sd) == {"cc-uuid": "Ship the thing"}


def test_payload_caches_and_writes_state(tmp_path, monkeypatch):
    cdir = tmp_path / "claude"
    write(cdir / "p" / "s.jsonl", [cc_line("m1")])
    monkeypatch.setattr(ui, "CLAUDE_DIR", cdir)
    monkeypatch.setattr(ui, "CODEX_DIR", tmp_path / "none")
    monkeypatch.setattr(ui, "CACHE_FILE", tmp_path / "usage.json")
    monkeypatch.setattr(ui, "STATE", tmp_path)
    monkeypatch.setattr(ui, "_mem", {"files": {}, "scanned_at": 0.0, "result": None, "result_at": 0.0, "thread": None})
    monkeypatch.setattr(ui, "chat_map", lambda: {})
    monkeypatch.setattr(ui, "HUB_DB", tmp_path / "no-state.db")
    r = ui.payload(wait=10)
    assert r["windows"]["today"]["tokens"] == 1110 and r["scanning"] is False
    assert (tmp_path / "usage.json").exists()
    assert ui.payload(wait=10) is ui._mem["result"]  # fresh: no rescan


def test_weekday_and_hour_charts_count_tokens_not_sessions(tmp_path):
    cdir = tmp_path / "claude"
    write(cdir / "a" / "s1.jsonl", [cc_line("m1", inp=5, out=50, cr=0), cc_line("m2", inp=5, out=50, cr=0)])
    r = ui.aggregate(ui.scan(cdir, tmp_path / "none", files={}), today=TODAY)
    w = r["windows"]["7d"]
    assert len(w["by_hour"]) == 24 and len(w["by_weekday"]) == 7
    assert w["by_hour"][12]["tokens"] == 110 and w["by_hour"][12]["out"] == 100
    assert sum(h["tokens"] for h in w["by_hour"]) == w["tokens"] == 110
    assert w["by_weekday"][TODAY.weekday()]["tokens"] == 110 == w["by_weekday"][TODAY.weekday()]["claude"]
    assert w["by_hour"][12]["codex"] == 0 and w["by_hour"][12]["hub"] == 0
    assert w["by_hour"][12]["cost"] == pytest.approx(w["cost"])
    assert sum(w["cost_parts"].values()) == pytest.approx(w["cost"], abs=1e-3)
    assert w["cost_parts"]["out"] == pytest.approx(100 * 20 / 1e6, abs=1e-4)


def test_codex_turn_past_272k_uses_long_context_price(tmp_path):
    e = {}
    lines = cx_lines(totals=((300_000, 0, 1000),))
    ui.parse_codex_lines([x.encode() for x in lines], e)
    (key, row), = e["rows"].items()
    assert key.endswith("|1")
    r = ui.aggregate({"f": {"kind": "codex", "cwd": "/x/p", "sid": "t", **e}}, today=TODAY)
    assert r["windows"]["today"]["cost"] == pytest.approx((300_000 * 4 + 1000 * 15) / 1e6, abs=1e-4)


def _hub_db(path, now):
    import sqlite3
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE sessions (id TEXT, source TEXT, title TEXT, cwd TEXT, started_at REAL, ended_at REAL, last_activity_at REAL);
        CREATE TABLE messages (session_id TEXT, role TEXT, timestamp REAL);
        CREATE TABLE session_model_usage (session_id TEXT, model TEXT, task TEXT, api_call_count INT, input_tokens INT,
            output_tokens INT, cache_read_tokens INT, cache_write_tokens INT, first_seen REAL, last_seen REAL);""")
    c.execute("INSERT INTO sessions VALUES ('w1','webui','Plan the trip','/x/alpha',?,NULL,?)", (now - 7200, now))
    c.execute("INSERT INTO sessions VALUES ('o1','oneshot',NULL,'/private/tmp',?,NULL,?)", (now - 60, now))
    for t in (now - 7200, now - 3600, now - 3600 + 5):
        c.execute("INSERT INTO messages VALUES ('w1','assistant',?)", (t,))
    c.execute("INSERT INTO messages VALUES ('w1','user',?)", (now - 7300,))
    c.execute("INSERT INTO session_model_usage VALUES ('w1','gpt-6.1-sol','',3,3001,31,9000,0,?,?)", (now - 7201, now - 3594))
    c.execute("INSERT INTO session_model_usage VALUES ('w1','gpt-6.1-sol','title_generation',1,100,7,0,0,?,?)", (now - 7000, now - 7000))
    c.execute("INSERT INTO session_model_usage VALUES ('o1','gpt-6.1-sol','',1,400,4,0,0,?,?)", (now - 60, now - 60))
    c.commit(); c.close()


def test_hub_tokens_spread_over_assistant_messages(tmp_path):
    now = dt.datetime.combine(TODAY, dt.time(14, 30)).timestamp()
    db = tmp_path / "state.db"
    _hub_db(db, now)
    rows = ui.hub_rows(db, since=now - 86400)
    main = [r for r in rows if r[4] == "chat:Plan the trip" and r[7] != 7]
    assert len(main) == 3 and sum(r[6] for r in main) == 3001 and sum(r[8] for r in main) == 9000
    assert sorted(r[1] for r in main) == [12, 13, 13]
    title = [r for r in rows if r[7] == 7]
    assert len(title) == 1  # a task without its own messages lands on last_seen
    one = [r for r in rows if r[4].startswith("hub:")]
    assert one[0][4] == "hub:Background tasks" and one[0][3] == "/private/tmp"
    r = ui.aggregate({}, today=TODAY, hub=rows)
    w = r["windows"]["today"]
    assert w["by_source"]["hub"]["tokens"] == 3001 + 31 + 9000 + 107 + 404
    assert w["cost"] == pytest.approx(ui.cost_of("gpt-6.1-sol", {"in": 3001 + 100 + 400, "out": 31 + 7 + 4, "cr": 9000}), abs=1e-4)
    names = {c["name"] for c in w["by_chat"]}
    assert "Plan the trip" in names and "Background tasks · hub" in names
    assert {p["name"] for p in w["by_project"]} == {"alpha", "Temporary folder"}
    assert r["by_day"][-1]["hub"]["tokens"] == w["by_source"]["hub"]["tokens"]
    assert ui.hub_rows(tmp_path / "missing.db") == []


def test_project_names_windows_style(monkeypatch, tmp_path):
    # Windows: home and %TEMP% compare without case, and a project folder is named by its last part
    import os
    monkeypatch.setattr(ui.os.path, "normcase", lambda s: s.lower())
    monkeypatch.setattr(ui.tempfile, "gettempdir", lambda: str(tmp_path / "Temp"))
    monkeypatch.setattr(ui.Path, "home", classmethod(lambda cls: tmp_path / "Alex"))
    assert ui._project(str(tmp_path / "ALEX")) == "Home folder"
    assert ui._project(str(tmp_path / "temp" / "x")) == "Temporary folder"
    assert ui._project(str(tmp_path / "Alex" / "acme-app")) == "acme-app"
