from __future__ import annotations
import importlib.util, json
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "plugin" / "limits"
spec = importlib.util.spec_from_file_location("limits_plugin", PKG / "__init__.py",
                                              submodule_search_locations=[str(PKG)])
L = importlib.util.module_from_spec(spec); spec.loader.exec_module(L)

CODEX_LINE = json.dumps({"timestamp": "2026-10-28T10:21:33.895Z", "type": "event_msg", "payload": {
    "type": "token_count", "info": {"total_token_usage": {"total_tokens": 18900}},
    "rate_limits": {"limit_id": "codex", "primary": {"used_percent": 8.0, "window_minutes": 300, "resets_at": 1793199576},
                    "secondary": {"used_percent": 1.0, "window_minutes": 10080, "resets_at": 1793786376},
                    "plan_type": "plus"}}})

CLAUDE = {"five_hour": {"utilization": 2.0, "resets_at": "2026-10-28T19:50:00.465918+00:00"},
          "seven_day": {"utilization": 78.0, "resets_at": "2026-11-01T09:00:00+00:00"},
          "extra_usage": {"is_enabled": False},
          "limits": [{"kind": "session", "percent": 2, "resets_at": "2026-10-28T19:50:00+00:00", "is_active": False},
                     {"kind": "weekly_all", "percent": 78, "resets_at": "2026-11-01T09:00:00+00:00", "is_active": True},
                     {"kind": "weekly_scoped", "percent": 0, "resets_at": "2026-11-01T09:00:00+00:00",
                      "scope": {"model": {"display_name": "Fable"}}}]}


def test_codex_token_count_line():
    hit = L.parse_codex_token_count(CODEX_LINE)
    assert hit["plan"] == "plus"
    assert [(w["window"], w["pct"]) for w in hit["windows"]] == [("5h", 8.0), ("7d", 1.0)]
    assert hit["windows"][0]["resets_at"] == 1793199576
    assert abs(hit["ts"] - 1793182893.895) < 1


def test_codex_non_token_lines_ignored():
    assert L.parse_codex_token_count('{"type":"event_msg","payload":{"type":"agent_message"}}') is None
    assert L.parse_codex_token_count("not json") is None
    no_rl = json.dumps({"payload": {"type": "token_count", "rate_limits": None}})
    assert L.parse_codex_token_count(no_rl) is None


def test_latest_session_file(tmp_path):
    d = tmp_path / "2026" / "10" / "28"; d.mkdir(parents=True)
    (d / "rollout-a.jsonl").write_text('{"payload":{"type":"user_message"}}\n' + CODEX_LINE + "\n", encoding="utf-8")
    hit = L.latest_codex_session_limits(tmp_path)
    assert hit and hit["windows"][0]["pct"] == 8.0


def test_codex_live_usage():
    info = L.parse_codex_usage({"plan_type": "plus", "rate_limit": {
        "limit_reached": False,
        "primary_window": {"used_percent": 0, "limit_window_seconds": 18000, "reset_at": 1793217607},
        "secondary_window": {"used_percent": 3, "limit_window_seconds": 604800, "reset_at": 1793786376}},
        "model_usage": {"gpt-6-astra": {"available": True}}})
    assert [(w["window"], w["pct"]) for w in info["windows"]] == [("5h", 0), ("7d", 3)]
    assert info["blocked_models"] == []


def test_claude_usage_limits_array():
    rows = L.parse_claude_usage(CLAUDE, "Claude Max 5x")
    assert [(r["window"], r["pct"]) for r in rows] == [("5h session", 2), ("7d all models", 78), ("7d Fable", 0)]
    assert rows[1]["note"] == "binding"
    assert rows[0]["resets_at"] == L._to_epoch("2026-10-28T19:50:00+00:00")


def test_claude_usage_legacy_shape():
    legacy = {k: v for k, v in CLAUDE.items() if k != "limits"}
    rows = L.parse_claude_usage(legacy)
    assert [(r["window"], r["pct"]) for r in rows] == [("5h session", 2.0), ("7d all models", 78.0)]


def test_format_and_stale_marker():
    rows = L.parse_claude_usage(CLAUDE) + [L._row("ChatGPT · Codex", "5h", pct=8.0, resets_at=1000 + 3600, stale=7200)]
    md = L.format_markdown(rows, now=1000)
    assert "| Claude Max | 5h session | 2% |" in md
    assert "stale 2h 0m" in md and "1h 0m" in md
    assert "Resets in" in L.format_plain(rows, now=1000)
    rd = L.format_readable(rows, now=1000)
    assert "- This session — 2% used" in rd and "from the last Codex session, 2h 0m ago" in rd
    assert "| " not in rd


def test_elevenlabs_and_higgsfield():
    r = L.parse_elevenlabs_subscription({"tier": "creator", "character_count": 25000, "character_limit": 100000,
                                         "next_character_count_reset_unix": 1793786376})[0]
    assert r["pct"] == 25.0 and "25,000 / 100,000 (25%)" == L._used(r)
    h = L.parse_higgsfield({"credits": 243, "subscription_plan_type": "pro"})[0]
    assert h["service"] == "Higgsfield Pro" and L._used(h) == "243 credits left"


def test_command_surfaces(monkeypatch):
    rows = [L._row("Higgsfield Pro", "credits", note="243 credits left")]
    monkeypatch.setattr(L, "collect", lambda refresh=False: rows)
    web = L._cmd_limit("")
    assert web.startswith("```tabbie-limits\n")
    block = json.loads(web.split("\n")[1])
    assert block["v"] == 1 and block["rows"][0]["service"] == "Higgsfield Pro"
    assert "**Higgsfield Pro**" in web and "- 243 credits left" in web
    assert L._cmd_limit("", platform="api_server").startswith("**Usage limits**")
    assert L._cmd_limit("", platform="cli").startswith("Service")
    assert L._cmd_limit("", platform="telegram").startswith("```")
    assert json.loads(L._tool_usage_limits({}))["limits"][0]["service"] == "Higgsfield Pro"


def test_limit_shows_connected_accounts_only():
    keep = [{"note": ""}, {"note": "binding"}, {"note": "12 credits left"}, {"note": L.SLOW},
            {"note": "Claude Code token expired — run `claude` once to refresh"}]
    drop = [{"note": "not connected — fix: `elevenlabs auth login`"}, {"note": "CLI not installed"},
            {"note": "no data — fix: `codex login`"}, {"note": "unavailable (not logged in) — fix: `higgsfield auth login`"}]
    assert all(L.connected(r) for r in keep)
    assert not any(L.connected(r) for r in drop)


def test_codex_worker_rows_hidden_when_codex_off(monkeypatch):
    rows = [L._row(f"ChatGPT Plus · {L.CODEX_LABEL}", "5h", pct=8.0),
            L._row("ChatGPT Plus · Hub gpt-6.1-sol", "5h", pct=3.0),
            L._row("ChatGPT Plus · Codex + Hub (shared pool)", "7d", pct=5.0)]
    monkeypatch.setattr(L, "SOURCES", (lambda: rows,))
    monkeypatch.setattr(L, "_enabled_agents", lambda: ["claude"])
    monkeypatch.setitem(L._cache, "rows", None)
    monkeypatch.setitem(L._cache, "at", 0)
    services = [r["service"] for r in L.collect(refresh=True)]
    assert services == ["ChatGPT Plus · Hub gpt-6.1-sol", "ChatGPT Plus · Codex + Hub (shared pool)"]
