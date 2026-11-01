"""Request + session state (JSON on disk, survives restarts).

Request lifecycle:
  Route A: new -> executing -> done | failed
  Route B: new -> planning -> awaiting_answers | awaiting_approval -> approved
           -> executing -> done | failed      (cancelled possible from any open state)
Execution of a Route B request is refused in code unless status == approved.
"""

from __future__ import annotations

import secrets
import time
from pathlib import Path

from .common import REQUESTS_DIR, SESSIONS_DIR, read_json, write_json

OPEN_PLANNING = {"planning", "awaiting_answers", "awaiting_approval"}


def new_request_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)


class Request:
    def __init__(self, request_id: str):
        self.id = request_id
        self.dir = REQUESTS_DIR / request_id
        self.file = self.dir / "request.json"
        self.data = read_json(self.file, None)

    @classmethod
    def create(cls, *, session_id: str, route: str, reason: str, forced: bool, prompt: str,
               raw_message: str, project: str | None, project_source: str) -> "Request":
        r = cls(new_request_id())
        r.dir.mkdir(parents=True, exist_ok=True)
        r.data = {
            "id": r.id,
            "session_id": session_id,
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "route": route,
            "route_reason": reason,
            "forced": forced,
            "status": "new",
            "original_prompt": prompt,          # verbatim, override prefix removed
            "raw_message": raw_message,         # exactly as typed
            "project": project,
            "project_source": project_source,
            "planner": None,                    # filled with provider/model actually used
            "planning_transcript": [],          # [{role: user|planner, content}]
            "pending_reply": None,
            "modifications": [],
            "proposal": None,
            "brief": None,
            "executor": None,
            "result": None,
        }
        r.save()
        return r

    @property
    def exists(self) -> bool:
        return self.data is not None

    def save(self) -> None:
        self.data["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        write_json(self.file, self.data)

    def __getitem__(self, k):
        return self.data[k]

    def __setitem__(self, k, v):
        self.data[k] = v

    def write_text(self, name: str, text: str) -> Path:
        p = self.dir / name
        p.write_text(text)
        return p


def session_state(session_id: str) -> dict:
    return read_json(SESSIONS_DIR / f"{_safe(session_id)}.json", {}) or {}


def save_session_state(session_id: str, data: dict) -> None:
    write_json(SESSIONS_DIR / f"{_safe(session_id)}.json", data)


def active_planning_request(session_id: str) -> Request | None:
    rid = session_state(session_id).get("active_request")
    if not rid:
        return None
    r = Request(rid)
    if r.exists and r["status"] in OPEN_PLANNING | {"approved"}:
        return r
    return None


def set_active(session_id: str, request_id: str | None) -> None:
    st = session_state(session_id)
    st["active_request"] = request_id
    save_session_state(session_id, st)


def _safe(s: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in (s or "default"))[:120]
