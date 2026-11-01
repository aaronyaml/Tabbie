"""Workboard plugin: store + tool layer against a throwaway kanban home (needs the Hermes Agent checkout)."""
import importlib.util, json, os, sys
from pathlib import Path

import pytest

AGENT = Path(os.path.expanduser("~/.hermes/hermes-agent"))
PKG = Path(__file__).resolve().parents[1] / "plugin" / "workboard"
pytestmark = pytest.mark.skipif(not (AGENT / "hermes_cli" / "kanban_db.py").exists(),
                                reason="Hermes Agent checkout not found")


@pytest.fixture()
def wb(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_KANBAN_BOARD", raising=False)
    spec = importlib.util.spec_from_file_location("workboard", PKG / "__init__.py",
                                                  submodule_search_locations=[str(PKG)])
    m = importlib.util.module_from_spec(spec); sys.modules["workboard"] = m; spec.loader.exec_module(m)
    return m


class Ctx:
    def __init__(self):
        self.tools, self.cmds, self.hooks = {}, {}, {}

    def register_tool(self, name, handler, **kw):
        self.tools[name] = handler

    def register_command(self, name, fn, **kw):
        self.cmds[name] = fn

    def register_hook(self, name, fn):
        self.hooks[name] = fn


def call(ctx, name, **p):
    out = json.loads(ctx.tools[name](p))
    assert out["ok"], out
    return out


def test_body_footer_roundtrip(wb):
    s = wb.store
    body = s.join_body("Line one\n\nLine two", "2026-11-01", ["ui", "hermes"])
    assert body.endswith("Due: 2026-11-01\nTags: ui, hermes")
    assert s.split_body(body) == ("Line one\n\nLine two", "2026-11-01", ["ui", "hermes"])
    assert s.split_body(None) == ("", None, [])
    assert s.join_body("", None, []) is None


def test_status_normalisation(wb):
    s = wb.store
    assert s.norm_status("kanban", "To do") == "ready"
    assert s.norm_status("kanban", "later") == "scheduled"
    assert s.norm_status("todo", "completed") == "done"
    assert s.norm_status("todo", "cancelled") == "archived"
    for bad in ("nonsense", "triage", "running"):  # dispatcher-owned or unknown
        with pytest.raises(ValueError):
            s.norm_status("kanban", bad)


def test_kanban_crud_and_actions(wb):
    ctx = Ctx(); wb.register(ctx)
    assert {"workboard_list", "workboard_create", "workboard_update", "workboard_note", "workboard_move",
            "workboard_complete", "workboard_reopen", "workboard_delete", "workboard_get"} <= set(ctx.tools)
    card = call(ctx, "workboard_create", kind="kanban", title="Ship board", priority="high",
                due="2026-11-01", tags=["ui"])["item"]
    assert card["status"] == "ready" and card["priority"] == 3 and card["embed"] == f"[[kanban:{card['id']}]]"
    cid = card["id"]
    # kind is inferred from the id when omitted
    up = call(ctx, "workboard_update", id=cid, title="Ship workboard", description="Body", tags=[],
              note="first note")["item"]
    assert (up["title"], up["description"], up["tags"], up["due"]) == ("Ship workboard", "Body", [], "2026-11-01")
    assert up["notes"][-1]["body"] == "first note"
    for col in ("blocked", "ready", "scheduled", "done", "scheduled", "ready"):
        assert call(ctx, "workboard_move", id=cid, column=col)["item"]["status"] == col
    assert call(ctx, "workboard_note", id=cid, text="second")["item"]["notes"][-1]["body"] == "second"
    assert call(ctx, "workboard_complete", id=cid)["item"]["status"] == "done"
    assert call(ctx, "workboard_reopen", id=cid)["item"]["status"] == "ready"
    assert call(ctx, "workboard_delete", id=cid)["item"]["status"] == "archived"
    assert call(ctx, "workboard_list", kind="kanban")["count"] == 0
    assert call(ctx, "workboard_list", kind="kanban", include_archived=True)["count"] == 1
    bad = json.loads(ctx.tools["workboard_move"]({"id": cid, "column": "running"}))
    assert not bad["ok"]


def test_todos_live_on_own_board_and_feed_panel(wb):
    ctx = Ctx(); wb.register(ctx)
    out = call(ctx, "workboard_create", kind="todo", titles=["Buy milk", "Call Sam"])
    ids = [i["id"] for i in out["items"]]
    assert all(i["board"] == "todos" for i in out["items"])
    assert [t["content"] for t in out["todos"]] == ["Buy milk", "Call Sam"]  # WebUI Todo panel snapshot
    assert out["summary"]["pending"] == 2
    done = call(ctx, "workboard_complete", id=ids[0])
    assert done["item"]["todo_status"] == "completed"
    assert {t["id"]: t["status"] for t in done["todos"]}[ids[0]] == "completed"
    # completing one todo must not disturb the others (todo->ready promotion is avoided by design)
    assert call(ctx, "workboard_get", id=ids[1])["item"]["status"] == "ready"
    assert call(ctx, "workboard_move", id=ids[1], column="later")["item"]["status"] == "scheduled"
    assert call(ctx, "workboard_list", kind="kanban")["count"] == 0  # boards stay separate


def test_slash_commands_and_hook(wb):
    ctx = Ctx(); wb.register(ctx)
    assert "No cards" in ctx.cmds["board"]("")
    item = call(ctx, "workboard_create", kind="kanban", title="A [weird] | title")["item"]
    text = ctx.cmds["board"]("")
    assert f"[[kanban:{item['id']}|A [weird) / title]]" in text and "**Ready**" in text
    hook = ctx.hooks["pre_llm_call"]
    assert "workboard_" in hook(user_message="add buy milk to my todos")
    assert hook(user_message="what's the weather") is None
    assert hook(user_message="/board") is None
