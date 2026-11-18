"""Settings → Team (team_link.py): only well-formed input reaches bin/tt, and Tabbie never shows a waiting code."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("team_link", ROOT / "plugin" / "command-centre" / "team_link.py")
team_link = importlib.util.module_from_spec(spec)
spec.loader.exec_module(team_link)


def fake(monkeypatch, answers):
    calls = []

    def run(args, timeout=40):
        calls.append(args)
        return answers.get(args[0], (0, "{}", ""))

    monkeypatch.setattr(team_link, "_run", run)
    monkeypatch.setattr(team_link, "tt_path", lambda: "/bin/true")
    monkeypatch.setattr(team_link, "_projects", lambda: ["Demo", "Private"])
    monkeypatch.setattr(team_link, "_config", lambda: {"relay": "https://r.example"})
    team_link._CACHE.clear()
    return calls


def test_bad_code_never_runs_tt(monkeypatch):
    calls = fake(monkeypatch, {})
    code, body = team_link.post("/team/approve", {"code": "ABCD; rm -rf ~"})
    assert code == 400 and not calls


def test_code_is_normalised(monkeypatch):
    calls = fake(monkeypatch, {"approve": (0, '{"ok": true}', ""), "info": (0, '{"linked": false}', "")})
    code, _ = team_link.post("/team/approve", {"code": "abcd efgh jkmn"})
    assert code == 200 and calls[0] == ["approve", "ABCD-EFGH-JKMN", "--json"]


def test_wrong_code_reads_plainly(monkeypatch):
    fake(monkeypatch, {"approve": (1, "", "tt: No waiting device shows X. Waiting: Phone (AAAA-BBBB-CCCC)\n")})
    code, body = team_link.post("/team/approve", {"code": "ZZZZ-ZZZZ-ZZZZ"})
    assert code == 400 and "AAAA" not in body["error"] and "doesn't match" in body["error"]


def test_share_only_known_projects(monkeypatch):
    calls = fake(monkeypatch, {"info": (0, '{"linked": false}', "")})
    assert team_link.post("/team/share", {"project": "../etc", "on": True})[0] == 400 and not calls
    team_link.post("/team/share", {"project": "Demo", "on": False})
    assert calls[0] == ["unshare", "Demo", "--json"]


def test_connect_rejects_junk(monkeypatch):
    calls = fake(monkeypatch, {})
    assert team_link.post("/team/connect", {"url": "https://a b"})[0] == 400 and not calls


def test_status_lists_waiting_names_only(monkeypatch):
    fake(monkeypatch, {"info": (0, json.dumps({"relay": "https://r.example", "linked": True}), ""),
                       "pending": (0, json.dumps({"pending": [{"name": "Phone", "since": 1}]}), "")})
    code, st = team_link.status(force=True)
    assert code == 200 and st["pending"] == [{"name": "Phone", "since": 1}] and st["all_projects"] == ["Demo", "Private"]


def test_not_installed(monkeypatch):
    monkeypatch.setattr(team_link, "tt_path", lambda: None)
    team_link._CACHE.clear()
    assert team_link.status(force=True) == (200, {"installed": False})


def test_no_team_answers_without_running_tt(monkeypatch):
    calls = fake(monkeypatch, {})
    monkeypatch.setattr(team_link, "_config", lambda: {})
    code, st = team_link.status(force=True)
    assert code == 200 and st["relay"] is None and st["all_projects"] == ["Demo", "Private"] and not calls
