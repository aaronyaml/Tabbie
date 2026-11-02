"""Chat messages reach a running Claude Code worker (providers.LiveInput), tested with a fake worker."""
from __future__ import annotations
import importlib.util, json, sys, textwrap, time
from importlib import import_module
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "plugin" / "command-centre"
if "command_centre" not in sys.modules:
    spec = importlib.util.spec_from_file_location("command_centre", PKG / "__init__.py",
                                                  submodule_search_locations=[str(PKG)])
    _m = importlib.util.module_from_spec(spec); sys.modules["command_centre"] = _m
    spec.loader.exec_module(_m)
pv = import_module("command_centre.providers")

# Echoes every user message it reads; ends a turn with a result 1 s after the first message, and
# ends an interrupted step with an empty result straight away (like `claude -p --input-format stream-json`).
FAKE = textwrap.dedent("""
    import json, sys, time
    seen = []
    for line in sys.stdin:
        ev = json.loads(line)
        if ev.get("type") == "control_request":
            print(json.dumps({"type": "result", "result": ""}), flush=True); continue
        text = ev["message"]["content"][0]["text"]
        seen.append(text)
        print(json.dumps({"type": "assistant", "text": text}), flush=True)
        if len(seen) == 1:
            time.sleep(1.5)
        print(json.dumps({"type": "result", "result": " | ".join(seen)}), flush=True)
""")


def _run(tmp_path, after):
    fake = tmp_path / "fake.py"; fake.write_text(FAKE)
    live = pv.LiveInput(tmp_path)
    import threading
    threading.Timer(0.3, after).start()
    code, out, _ = pv._run_stream([sys.executable, str(fake)], cwd=str(tmp_path), env=None, timeout=30,
                                  stdin="do the task", on_line=lambda _l: None, live=live)
    results = [json.loads(l)["result"] for l in out.splitlines() if '"result"' in l]
    acks = (tmp_path / "inbox-acks.jsonl").read_text() if (tmp_path / "inbox-acks.jsonl").exists() else ""
    return code, results, acks


def test_queued_message_reaches_the_running_worker(tmp_path):
    inbox = tmp_path / "inbox.jsonl"
    code, results, acks = _run(tmp_path, lambda: inbox.write_text(json.dumps({"id": "a1", "text": "also add BANANA", "mode": "queue"}) + "\n"))
    assert code == 0
    assert any("also add BANANA" in r for r in results)
    assert '"a1"' in acks
    assert json.loads((tmp_path / "live.json").read_text())["open"] is False


def test_send_now_interrupts_then_runs_the_message(tmp_path):
    inbox = tmp_path / "inbox.jsonl"
    code, results, acks = _run(tmp_path, lambda: inbox.write_text(json.dumps({"id": "b2", "text": "MANGO", "mode": "now"}) + "\n"))
    assert code == 0 and '"b2"' in acks
    assert "MANGO" in results[-1]  # the interrupted step's empty result didn't close stdin


def test_messages_before_the_run_are_not_replayed(tmp_path):
    (tmp_path / "inbox.jsonl").write_text(json.dumps({"id": "old", "text": "stale", "mode": "queue"}) + "\n")
    code, results, acks = _run(tmp_path, lambda: None)
    assert code == 0 and all("stale" not in r for r in results) and '"old"' not in acks


def test_delivered_messages_reach_run_claudes_result(tmp_path, monkeypatch):
    """Issue: the hub relayed only the first answer; run_claude must say what the user added mid-run."""
    fake = tmp_path / "fake.py"; fake.write_text(FAKE)
    monkeypatch.setattr(pv, "claude_auth", lambda: (True, "sub"))
    monkeypatch.setattr(pv, "build_claude_cmd", lambda *a, **k: [sys.executable, str(fake)])
    monkeypatch.setattr(pv, "LOCKS_DIR", tmp_path / "locks")
    inbox = tmp_path / "inbox.jsonl"
    import threading
    threading.Timer(0.3, lambda: inbox.write_text(json.dumps({"id": "c3", "text": "  also count .js  ", "mode": "queue"})
                                                  + "\n")).start()
    res = pv.run_claude("do the task", cwd=str(tmp_path), add_dirs=[], cfg={}, resume_session=None,
                        on_progress=lambda _t: None, inbox=tmp_path)
    assert res["ok"] and res["live_messages"] == ["also count .js"]
    assert "also count .js" in res["result"]


def test_no_live_messages_without_an_inbox(tmp_path):
    live = pv.LiveInput(tmp_path)
    assert live.delivered == []
