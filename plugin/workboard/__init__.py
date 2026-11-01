"""Workboard — Hermes plugin: kanban cards + todos shared with the WebUI.

Store: Hermes Agent's own kanban DB (see store.py) — the same one the WebUI's Kanban panel renders,
so every write here appears there live. Todos live on a dedicated ``todos`` board; tool results for
todos also carry a ``{todos, summary}`` snapshot, which the WebUI's Todo panel picks up.

Tools  : workboard_list, workboard_get, workboard_create, workboard_update, workboard_note,
         workboard_move, workboard_complete, workboard_reopen, workboard_delete
Hook   : pre_llm_call — on task/todo/kanban talk, tells the hub to handle it itself with these tools.
Slash  : /board (kanban cards), /todos (todos) — print items as embed tokens the WebUI renders.
Embeds : ``[[kanban:<id>]]`` / ``[[todo:<id>]]`` (optional ``|label``) in a reply render as live cards
         (~/hermes/webui-extension/workboard.js).
"""

from __future__ import annotations

import json
import re

from . import store

TAG = "[workboard]"
_INTENT = re.compile(
    r"\b(kanban|todo'?s?|to-?dos?|to do list|task ?list|my tasks|checklist|board|cards?)\b"
    r"|\[\[(kanban|todo):", re.I)


def _ok(payload: dict) -> str:
    return json.dumps({"ok": True, **payload}, ensure_ascii=False, default=str)


def _err(exc: Exception) -> str:
    return json.dumps({"ok": False, "error": str(exc) or exc.__class__.__name__})


def _kind_of(p: dict) -> str:
    if p.get("kind"):
        return p["kind"]
    if p.get("id"):
        return store.find_kind(str(p["id"]))
    return "kanban"


def _with_snapshot(kind: str, payload: dict) -> dict:
    """Todo results carry the full list so the WebUI Todo panel mirrors the store."""
    if store._kind(kind) == "todo":
        payload.update(store.todo_snapshot(store.list_items("todo")))
    payload["hint"] = "Embed items in your reply with their `embed` token, e.g. [[kanban:t_ab12cd34]]."
    return payload


def _handler(fn):
    def run(p, **kw):
        try:
            return fn(p or {})
        except Exception as exc:  # tools must answer, never raise
            return _err(exc)
    return run


def _list(p):
    kind = store._kind(p.get("kind") or "kanban")
    items = store.list_items(kind, status=p.get("status"), query=p.get("query"),
                             include_done=p.get("include_done", True) is not False,
                             include_archived=bool(p.get("include_archived")), limit=p.get("limit") or 100)
    return _ok(_with_snapshot(kind, {"kind": kind, "count": len(items), "items": items}))


def _get(p):
    kind = _kind_of(p)
    return _ok({"item": store.get_item(kind, p["id"])})


def _create(p):
    kind = store._kind(p.get("kind") or "kanban")
    titles = p.get("titles")
    if titles:
        items = [store.create_item(kind, t, p.get("description"), p.get("status"), p.get("priority"),
                                   p.get("due"), p.get("tags")) for t in titles if str(t).strip()]
        return _ok(_with_snapshot(kind, {"items": items}))
    item = store.create_item(kind, p.get("title"), p.get("description"), p.get("status"),
                             p.get("priority"), p.get("due"), p.get("tags"))
    return _ok(_with_snapshot(kind, {"item": item}))


def _update(p):
    kind = _kind_of(p)
    item = store.update_item(kind, p["id"], title=p.get("title"), description=p.get("description"),
                             status=p.get("status") or p.get("column"), priority=p.get("priority"),
                             due=p.get("due"), tags=p.get("tags"), note=p.get("note"))
    return _ok(_with_snapshot(kind, {"item": item}))


def _note(p):
    kind = _kind_of(p)
    return _ok({"item": store.add_note(kind, p["id"], p.get("text") or p.get("note") or "")})


def _move(p):
    kind = _kind_of(p)
    return _ok(_with_snapshot(kind, {"item": store.move_item(kind, p["id"], p.get("column") or p.get("status"),
                                                             p.get("note"))}))


def _complete(p):
    kind = _kind_of(p)
    return _ok(_with_snapshot(kind, {"item": store.complete_item(kind, p["id"], p.get("note"))}))


def _reopen(p):
    kind = _kind_of(p)
    return _ok(_with_snapshot(kind, {"item": store.reopen_item(kind, p["id"], p.get("column"))}))


def _delete(p):
    kind = _kind_of(p)
    return _ok(_with_snapshot(kind, {"item": store.archive_item(kind, p["id"]),
                                     "note": "Archived (recoverable with workboard_reopen)."}))


# ------------------------------------------------------------------ hook + slash commands

def _text_of(message) -> str:
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        return " ".join(p.get("text", "") for p in message if isinstance(p, dict))
    return ""


def _on_pre_llm_call(user_message=None, **kwargs):
    text = _text_of(user_message).strip()
    if not text or text.startswith("/") or not _INTENT.search(text):
        return None
    return (f"{TAG} This turn is about the user's kanban board / todos. Handle it YOURSELF with the "
            f"workboard_* tools (this overrides any routing directive above for the board/todo part; do "
            f"not dispatch board or todo edits to cc_* workers). After creating, changing or listing items, "
            f"put each item's embed token on its own line, e.g. [[kanban:t_ab12cd34]] or [[todo:t_ab12cd34]], "
            f"so the WebUI shows a live card; keep surrounding prose short.")


def _cmd(kind):
    def run(raw_args: str = "", **kwargs):
        try:
            q = (raw_args or "").strip()
            items = store.list_items(kind, query=q or None, include_done=kind == "todo" or bool(q))
            head = "Todos" if kind == "todo" else "Kanban board"
            return f"**{head}** ({len(items)})\n\n" + store.format_items(kind, items)
        except Exception as exc:
            return f"{TAG} error: {exc}"
    return run


def register(ctx):
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)

    kind_p = {"type": "string", "enum": ["kanban", "todo"],
              "description": "kanban = board cards; todo = the user's todo list. Optional when id is given."}
    id_p = {"type": "string", "description": "Item id, e.g. t_ab12cd34"}
    fields = {
        "title": {"type": "string"},
        "description": {"type": "string", "description": "Markdown body"},
        "status": {"type": "string", "description": "Column: ready (open / to do), scheduled (later), blocked, "
                                                    "done, archived. Todos also accept pending, completed, "
                                                    "cancelled. triage/running belong to the dispatcher."},
        "priority": {"type": ["integer", "string"], "description": "0-9 or low/medium/high/urgent"},
        "due": {"type": "string", "description": "YYYY-MM-DD; empty string clears"},
        "tags": {"type": "array", "items": {"type": "string"}, "description": "Replaces tags; [] clears"},
    }

    def tool(name, desc, props, required, fn):
        ctx.register_tool(name=name, toolset="workboard", emoji="", handler=_handler(fn),
                          schema={"name": name, "description": desc,
                                  "parameters": {"type": "object", "properties": props, "required": required}})

    tool("workboard_list",
         "List the user's kanban cards or todos (shared with the WebUI Kanban/Todo panels). Each item has id, "
         "title, status/column, priority, due, tags and an `embed` token to show it as a live card in chat.",
         {"kind": kind_p, "status": fields["status"], "query": {"type": "string", "description": "Text filter"},
          "include_done": {"type": "boolean"}, "include_archived": {"type": "boolean"},
          "limit": {"type": "integer"}}, [], _list)
    tool("workboard_get", "Get one kanban card or todo with its notes.", {"kind": kind_p, "id": id_p}, ["id"],
         _get)
    tool("workboard_create",
         "Create a kanban card or todo (new items start open, in 'ready'). Pass `titles` to add several at once. "
         "Embed the returned `embed` token(s) in your reply.",
         {"kind": kind_p, **fields, "titles": {"type": "array", "items": {"type": "string"},
                                               "description": "Create one item per title"}},
         ["kind"], _create)
    tool("workboard_update",
         "Update a card/todo: title, description, status/column, priority, due, tags; optional note is "
         "appended as a comment.",
         {"kind": kind_p, "id": id_p, **fields, "note": {"type": "string"}}, ["id"], _update)
    tool("workboard_note", "Append a note/comment to a card or todo.",
         {"kind": kind_p, "id": id_p, "text": {"type": "string"}}, ["id", "text"], _note)
    tool("workboard_move", "Move a card/todo to another column.",
         {"kind": kind_p, "id": id_p, "column": fields["status"], "note": {"type": "string"}},
         ["id", "column"], _move)
    tool("workboard_complete", "Mark a card/todo done (checks a todo off).",
         {"kind": kind_p, "id": id_p, "note": {"type": "string"}}, ["id"], _complete)
    tool("workboard_reopen", "Reopen a done or archived card/todo (back to 'ready' unless column given).",
         {"kind": kind_p, "id": id_p, "column": {"type": "string"}}, ["id"], _reopen)
    tool("workboard_delete", "Delete (archive, recoverable) a card or todo.",
         {"kind": kind_p, "id": id_p}, ["id"], _delete)

    ctx.register_command("board", _cmd("kanban"), description="Show the kanban board as live cards",
                         args_hint="[filter text]")
    ctx.register_command("todos", _cmd("todo"), description="Show the todo list as live cards",
                         args_hint="[filter text]")
