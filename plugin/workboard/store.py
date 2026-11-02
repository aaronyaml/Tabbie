"""Workboard store: kanban cards and todos on Hermes Agent's own kanban DB.

Both item kinds live in ``hermes_cli.kanban_db`` (the same SQLite store the WebUI's
Kanban panel renders through ``/api/kanban/*``), so a write here shows up live there:

- ``kanban`` items: tasks on the board named by ``WORKBOARD_KANBAN_BOARD`` (default ``default``).
- ``todo`` items:   tasks on a dedicated board ``WORKBOARD_TODO_BOARD`` (default ``todos``).

Boards are always passed explicitly, so switching the active board in the WebUI never
redirects writes. Tasks are never assigned, so the kanban dispatcher never spawns a worker
for them. ``due`` and ``tags`` have no column; they ride in a footer at the end of the task
body (``Due: YYYY-MM-DD`` / ``Tags: a, b``), which the WebUI panel shows as plain text and
the chat embeds parse back.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from contextlib import contextmanager

AGENT_DIR = os.path.expanduser(os.environ.get("HERMES_AGENT_DIR") or os.path.join(
    os.environ.get("HERMES_HOME") or (os.path.join(os.environ["LOCALAPPDATA"], "hermes")
                                      if sys.platform == "win32" and os.environ.get("LOCALAPPDATA") else "~/.hermes"),
    "hermes-agent"))
if AGENT_DIR not in sys.path and os.path.isdir(AGENT_DIR):
    sys.path.insert(0, AGENT_DIR)

KINDS = ("kanban", "todo")
COLUMNS = ["triage", "todo", "scheduled", "ready", "running", "blocked", "done"]
COLUMN_LABELS = {"triage": "Triage", "todo": "To do", "scheduled": "Scheduled", "ready": "Ready",
                 "running": "Running", "blocked": "Blocked", "done": "Done", "archived": "Archived"}
TODO_LABELS = {**COLUMN_LABELS, "ready": "To do", "archived": "Cancelled"}
# Stable columns only. On Hermes' kanban DB, ``todo`` auto-promotes to ``ready`` (recompute_ready),
# ``triage`` is auto-decomposed by an LLM (kanban.auto_decompose) and ``running`` belongs to the
# dispatcher's claim protocol (orphans are requeued). So personal items use ready/scheduled/blocked/done.
WRITABLE = ("ready", "scheduled", "blocked", "done", "archived")
# Todo statuses (the WebUI Todo panel vocabulary) <-> columns on the todos board.
TODO_TO_COLUMN = {"pending": "ready", "in_progress": "ready", "completed": "done", "cancelled": "archived"}
COLUMN_TO_TODO = {"todo": "pending", "triage": "pending", "scheduled": "pending", "blocked": "pending",
                  "ready": "pending", "running": "in_progress", "done": "completed",
                  "archived": "cancelled"}
STATUS_ALIASES = {"todo": "ready", "to do": "ready", "to-do": "ready", "open": "ready", "pending": "ready",
                  "new": "ready", "doing": "ready", "in progress": "ready", "in_progress": "ready",
                  "wip": "ready", "backlog": "scheduled", "later": "scheduled", "someday": "scheduled",
                  "waiting": "blocked", "on hold": "blocked", "complete": "done", "completed": "done",
                  "closed": "done", "finished": "done", "deleted": "archived", "cancelled": "archived",
                  "canceled": "archived"}
PRIORITY_WORDS = {"none": 0, "low": 1, "medium": 2, "normal": 2, "high": 3, "urgent": 4}
AUTHOR = "hermes"
_FOOT_RE = re.compile(r"^(Due|Tags):[ \t]*(.*)$", re.I)


def board_for(kind: str) -> str:
    kind = _kind(kind)
    if kind == "todo":
        return os.environ.get("WORKBOARD_TODO_BOARD", "todos")
    return os.environ.get("WORKBOARD_KANBAN_BOARD", "default")


def _kind(kind) -> str:
    k = str(kind or "kanban").strip().lower().rstrip("s")
    if k in ("card", "board", "task", "kanban"):
        return "kanban"
    if k in ("todo", "to-do", "checklist"):
        return "todo"
    raise ValueError(f"kind must be 'kanban' or 'todo', got {kind!r}")


def _kb():
    from hermes_cli import kanban_db as kb
    return kb


@contextmanager
def _conn(kind: str):
    kb = _kb()
    board = board_for(kind)
    if board != getattr(kb, "DEFAULT_BOARD", "default") and not kb.board_exists(board):
        from hermes_cli import kanban_db_boards as kbb
        kbb.create_board(board, name="Todos" if _kind(kind) == "todo" else board.title(),
                         description="Todos shared by Hermes and the WebUI (workboard plugin)")
    kb.init_db(board=board)
    from hermes_cli import kanban_db_connect as kbc
    with kbc.connect_closing(board=board) as conn:
        yield conn


# ------------------------------------------------------------------ body footer (due / tags)

def split_body(body: str | None) -> tuple[str, str | None, list[str]]:
    """Return (description, due, tags) from a task body."""
    lines = (body or "").rstrip().split("\n")
    due, tags = None, []
    while lines and _FOOT_RE.match(lines[-1].strip()):
        key, val = _FOOT_RE.match(lines[-1].strip()).groups()
        if key.lower() == "due":
            due = val.strip() or None
        else:
            tags = [t.strip().lstrip("#") for t in re.split(r"[,\s]+", val) if t.strip().lstrip("#")]
        lines.pop()
    return "\n".join(lines).rstrip(), due, tags


def join_body(description: str | None, due: str | None, tags: list[str] | None) -> str | None:
    foot = []
    if due:
        foot.append(f"Due: {due}")
    if tags:
        foot.append("Tags: " + ", ".join(tags))
    desc = (description or "").rstrip()
    text = desc + (("\n\n" if desc else "") + "\n".join(foot) if foot else "")
    return text or None


def _norm_tags(tags) -> list[str]:
    if tags is None:
        return []
    if isinstance(tags, str):
        tags = re.split(r"[,\s]+", tags)
    return [str(t).strip().lstrip("#") for t in tags if str(t).strip().lstrip("#")]


def _norm_due(due) -> str | None:
    due = str(due or "").strip()
    if not due or due.lower() in ("none", "null", "clear"):
        return None
    if not re.match(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2})?$", due):
        raise ValueError("due must be YYYY-MM-DD (optionally with HH:MM)")
    return due


def _norm_priority(p) -> int:
    if p is None or p == "":
        return 0
    if isinstance(p, str) and p.strip().lower() in PRIORITY_WORDS:
        return PRIORITY_WORDS[p.strip().lower()]
    try:
        return max(0, min(9, int(p)))
    except (TypeError, ValueError):
        raise ValueError("priority must be 0-9 or low/medium/high/urgent")


def norm_status(kind: str, status) -> str:
    """Map a column name / todo status / alias to a writable column."""
    s = str(status or "").strip().lower().replace("_", " ")
    s = {"in progress": "in_progress"}.get(s, s)
    if _kind(kind) == "todo" and s in TODO_TO_COLUMN:
        return TODO_TO_COLUMN[s]
    s = STATUS_ALIASES.get(s, s)
    if s in ("triage", "running"):
        raise ValueError(f"'{s}' is managed by the kanban dispatcher (auto-decompose / worker claims); "
                         f"use one of {list(WRITABLE)}")
    if s not in WRITABLE:
        raise ValueError(f"unknown status/column {status!r}; use one of {list(WRITABLE)}")
    return s


# ------------------------------------------------------------------ serialisation

def token(kind: str, item_id: str) -> str:
    return f"[[{_kind(kind)}:{item_id}]]"


def _item(kind: str, task, comments=None) -> dict:
    kind = _kind(kind)
    desc, due, tags = split_body(task.body)
    out = {"kind": kind, "id": task.id, "title": task.title, "description": desc, "status": task.status,
           "column": (TODO_LABELS if kind == "todo" else COLUMN_LABELS).get(task.status, task.status), "priority": task.priority or 0,
           "due": due, "tags": tags, "board": board_for(kind), "created_at": task.created_at,
           "completed_at": task.completed_at, "embed": token(kind, task.id)}
    if kind == "todo":
        out["todo_status"] = COLUMN_TO_TODO.get(task.status, "pending")
    if comments is not None:
        out["notes"] = [{"author": c.author, "body": c.body, "at": c.created_at} for c in comments]
    return out


def todo_snapshot(items: list[dict]) -> dict:
    """Shape the WebUI Todo panel reads from tool results ({todos, summary})."""
    todos = [{"id": i["id"], "content": i["title"], "status": i["todo_status"], "source": "workboard"}
             for i in items]
    summary = {"total": len(todos)}
    for k in ("pending", "in_progress", "completed", "cancelled"):
        summary[k] = sum(1 for t in todos if t["status"] == k)
    return {"todos": todos, "summary": summary}


# ------------------------------------------------------------------ status writes

def _set_status_direct(conn, task_id: str, new_status: str) -> bool:
    """Claim-aware raw status write (mirrors the WebUI kanban bridge's helper)."""
    kb = _kb()
    with kb.write_txn(conn):
        prev = conn.execute("SELECT status, current_run_id FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if prev is None:
            return False
        conn.execute(
            "UPDATE tasks SET status = ?, claim_lock = NULL, claim_expires = NULL, worker_pid = NULL, "
            "completed_at = CASE WHEN ? = 'done' THEN completed_at ELSE NULL END WHERE id = ?",
            (new_status, new_status, task_id))
        run_id = None
        if prev["status"] == "running" and prev["current_run_id"]:
            try:
                run_id = kb._end_run(conn, task_id, outcome="reclaimed", status="reclaimed",
                                     summary=f"status changed to {new_status} (workboard)")
            except Exception:
                run_id = None
        conn.execute("INSERT INTO task_events (task_id, run_id, kind, payload, created_at) "
                     "VALUES (?, ?, 'status', ?, ?)",
                     (task_id, run_id, json.dumps({"status": new_status, "source": "workboard"}),
                      int(time.time())))
    if new_status in ("done", "ready"):
        try:
            kb.recompute_ready(conn)
        except Exception:
            pass
    return True


def _apply_status(conn, task_id: str, status: str, note: str | None = None) -> None:
    kb = _kb()
    task = kb.get_task(conn, task_id)
    if task is None:
        raise LookupError(f"no item {task_id}")
    if task.status == status:
        return
    if status == "running":
        raise ValueError("'running' is owned by the kanban dispatcher; use 'ready' for in-progress work")
    if status == "done":
        if task.status not in ("ready", "running", "blocked", "review"):
            _set_status_direct(conn, task_id, "ready")
        ok = kb.complete_task(conn, task_id, result=note or "Marked done", force=True)
        if not ok:
            raise RuntimeError("could not complete (an unfinished parent card blocks it?)")
    elif status == "archived":
        if not kb.archive_task(conn, task_id):
            raise RuntimeError("archive refused")
    elif status == "blocked":
        if task.status not in ("ready", "running"):
            _set_status_direct(conn, task_id, "ready")
        if not kb.block_task(conn, task_id, reason=note or "Blocked"):
            raise RuntimeError("block refused")
    elif status == "scheduled":
        if task.status in ("done", "archived"):
            _set_status_direct(conn, task_id, "ready")
        if not kb.schedule_task(conn, task_id, reason=note or None):
            raise RuntimeError(f"cannot schedule from {task.status}")
    elif status == "ready" and task.status in ("blocked", "scheduled"):
        if not kb.unblock_task(conn, task_id):
            raise RuntimeError("unblock refused")
    else:
        _set_status_direct(conn, task_id, status)


# ------------------------------------------------------------------ public API

def _filter_status(kind: str, status) -> str:
    s = str(status).strip().lower()
    return s if s in COLUMNS or s == "archived" else norm_status(kind, status)


def list_items(kind: str = "kanban", status: str | None = None, query: str | None = None,
               include_done: bool = True, include_archived: bool = False, limit: int = 100) -> list[dict]:
    kind = _kind(kind)
    kb = _kb()
    want = _filter_status(kind, status) if status else None
    with _conn(kind) as conn:
        tasks = kb.list_tasks(conn, include_archived=include_archived or want == "archived")
    items = []
    for t in tasks:
        if want and t.status != want:
            continue
        if not include_done and t.status == "done" and want != "done":
            continue
        if query and query.lower() not in f"{t.title}\n{t.body or ''}".lower():
            continue
        items.append(_item(kind, t))
    order = {c: i for i, c in enumerate(COLUMNS + ["archived"])}
    items.sort(key=lambda i: (order.get(i["status"], 99), -i["priority"], i["created_at"] or 0))
    return items[: max(1, int(limit or 100))]


def get_item(kind: str, item_id: str) -> dict:
    kind = _kind(kind)
    kb = _kb()
    with _conn(kind) as conn:
        task = kb.get_task(conn, str(item_id).strip())
        if task is None:
            raise LookupError(f"no {kind} item {item_id}")
        return _item(kind, task, kb.list_comments(conn, task.id))


def find_kind(item_id: str) -> str:
    """Which board holds this id (ids are unique across boards in practice)."""
    for kind in KINDS:
        try:
            get_item(kind, item_id)
            return kind
        except LookupError:
            continue
    raise LookupError(f"no item {item_id} on either board")


def create_item(kind: str, title: str, description: str | None = None, status: str | None = None,
                priority=None, due=None, tags=None, author: str = AUTHOR) -> dict:
    kind = _kind(kind)
    title = str(title or "").strip()
    if not title:
        raise ValueError("title is required")
    kb = _kb()
    body = join_body(description, _norm_due(due), _norm_tags(tags))
    target = norm_status(kind, status) if status else "ready"
    with _conn(kind) as conn:
        task_id = kb.create_task(conn, title=title, body=body, created_by=author, priority=_norm_priority(priority),
                                 workspace_kind="scratch", board=board_for(kind))
        _apply_status(conn, task_id, target)
        return _item(kind, kb.get_task(conn, task_id))


def update_item(kind: str, item_id: str, *, title=None, description=None, status=None, priority=None,
                due=None, tags=None, note: str | None = None) -> dict:
    """Partial update. ``due``/``tags`` of "" clear them; None leaves them unchanged."""
    kind = _kind(kind)
    kb = _kb()
    with _conn(kind) as conn:
        task = kb.get_task(conn, item_id)
        if task is None:
            raise LookupError(f"no {kind} item {item_id}")
        desc0, due0, tags0 = split_body(task.body)
        new_title = None if title is None else (str(title).strip() or None)
        if title is not None and not new_title:
            raise ValueError("title cannot be empty")
        body = None
        if description is not None or due is not None or tags is not None:
            body = join_body(desc0 if description is None else description,
                             due0 if due is None else _norm_due(due),
                             tags0 if tags is None else _norm_tags(tags)) or ""
            if body == (task.body or ""):
                body = None
        prio = None if priority is None else _norm_priority(priority)
        if prio == task.priority:
            prio = None
        if new_title == task.title:
            new_title = None
        if any(v is not None for v in (new_title, body, prio)):
            kb.edit_task(conn, item_id, title=new_title, body=body, priority=prio, board=board_for(kind))
        if status:
            _apply_status(conn, item_id, norm_status(kind, status), note)
        if note:
            kb.add_comment(conn, item_id, AUTHOR, note)
        return _item(kind, kb.get_task(conn, item_id), kb.list_comments(conn, item_id))


def add_note(kind: str, item_id: str, text: str, author: str = AUTHOR) -> dict:
    kind = _kind(kind)
    text = str(text or "").strip()
    if not text:
        raise ValueError("note text is required")
    kb = _kb()
    with _conn(kind) as conn:
        if kb.get_task(conn, item_id) is None:
            raise LookupError(f"no {kind} item {item_id}")
        kb.add_comment(conn, item_id, author, text)
        return _item(kind, kb.get_task(conn, item_id), kb.list_comments(conn, item_id))


def move_item(kind: str, item_id: str, column: str, note: str | None = None) -> dict:
    return update_item(kind, item_id, status=column, note=note)


def complete_item(kind: str, item_id: str, note: str | None = None) -> dict:
    return update_item(kind, item_id, status="done", note=note)


def reopen_item(kind: str, item_id: str, column: str | None = None) -> dict:
    return update_item(kind, item_id, status=column or "ready")


def archive_item(kind: str, item_id: str) -> dict:
    return update_item(kind, item_id, status="archived")


def format_items(kind: str, items: list[dict]) -> str:
    """Markdown summary for slash commands: grouped by column, each line carries its embed token."""
    kind = _kind(kind)
    if not items:
        return f"No {'todos' if kind == 'todo' else 'cards'} yet."
    out, last = [], None
    for it in items:
        if it["column"] != last:
            last = it["column"]
            out.append(f"\n**{last}**")
        out.append(f"- [[{kind}:{it['id']}|{it['title'].replace(']', ')').replace('|', '/')}]]")
    return "\n".join(out).strip()
