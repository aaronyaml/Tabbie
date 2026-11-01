"""Structured user questions (Claude Code AskUserQuestion style) from headless workers.

Workers that are blocked on a user decision end their final message with:

    ```hermes-question
    {"questions": [{"header": "Scope", "question": "Which pages?", "multiSelect": false,
                    "options": [{"label": "Home only", "description": "Fastest"}, ...]}]}
    ```

1-4 questions; each has a short header chip (<=12 chars), 2-4 options (label + description),
optional multiSelect. The UI always adds a free-text "Other". The plugin parks the request in
awaiting_answer; the user's reply resumes the same Claude session / Astra run.
"""

from __future__ import annotations

import json
import re

FENCE = "hermes-question"
_BLOCK = re.compile(r"```[ \t]*hermes-question[ \t]*\n(.*?)\n?```", re.S | re.I)

QUESTION_RULES = """- If you are BLOCKED on a decision only the user can make (it materially changes the result and
  you cannot reasonably pick a default), stop and ask instead of guessing. End your final message
  with exactly one fenced block, nothing after it:
  ```hermes-question
  {"questions": [{"header": "<=12 chars", "question": "Full question?", "multiSelect": false,
    "options": [{"label": "1-5 words", "description": "What this choice means"}, ...]}]}
  ```
  1-4 questions, 2-4 options each (the user can also type their own answer). Put the
  recommended option first. Do not ask about things you can decide or verify yourself. The
  user's answers arrive as the next message in this same session; then continue the work."""

# Codex output-schema (strict): every key required, no extras.
QUESTION_ITEM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["header", "question", "multiSelect", "options"],
    "properties": {
        "header": {"type": "string"},
        "question": {"type": "string"},
        "multiSelect": {"type": "boolean"},
        "options": {
            "type": "array",
            "items": {"type": "object", "additionalProperties": False, "required": ["label", "description"],
                      "properties": {"label": {"type": "string"}, "description": {"type": "string"}}},
        },
    },
}


def _clip(s, n: int) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def normalize(raw) -> dict | None:
    """Coerce a worker's question payload into {questions: [...]} or None if unusable."""
    if isinstance(raw, dict) and "questions" in raw:
        qs = raw["questions"]
    elif isinstance(raw, dict) and "question" in raw:
        qs = [raw]
    elif isinstance(raw, list):
        qs = raw
    else:
        return None
    out = []
    for i, q in enumerate(qs if isinstance(qs, list) else []):
        if isinstance(q, str):
            q = {"question": q}
        if not isinstance(q, dict) or not str(q.get("question") or "").strip():
            continue
        opts = []
        for o in q.get("options") or []:
            if isinstance(o, str):
                o = {"label": o}
            if isinstance(o, dict) and str(o.get("label") or "").strip():
                opts.append({"label": _clip(o["label"], 60), "description": _clip(o.get("description"), 200)})
        out.append({"header": _clip(q.get("header") or f"Question {i + 1}", 16),
                    "question": _clip(q["question"], 400),
                    "multiSelect": bool(q.get("multiSelect") or q.get("multi_select")),
                    "options": opts[:4]})
    return {"questions": out[:4]} if out else None


def extract(text: str) -> tuple[str, dict | None]:
    """Find the last hermes-question block in a worker's final message.
    Returns (text without the block, normalized questions or None)."""
    if not text:
        return text or "", None
    matches = list(_BLOCK.finditer(text))
    if not matches:
        return text, None
    m = matches[-1]
    try:
        data = normalize(json.loads(m.group(1)))
    except json.JSONDecodeError:
        return text, None
    if not data:
        return text, None
    clean = (text[: m.start()] + text[m.end():]).strip()
    return clean, data


def block(data: dict, *, request_id: str, source: str, round: int = 1) -> str:
    """Render the fenced block the hub shows verbatim; the WebUI turns it into a question card."""
    payload = {"request_id": request_id, "source": source, "round": round, "questions": data["questions"]}
    return f"```{FENCE}\n{json.dumps(payload, ensure_ascii=False)}\n```"


def as_text(data: dict) -> str:
    """Plain-text fallback (CLI / clients without the card)."""
    lines = []
    for i, q in enumerate(data["questions"], 1):
        lines.append(f"{i}. [{q['header']}] {q['question']}" + (" (choose any)" if q["multiSelect"] else ""))
        for o in q["options"]:
            lines.append(f"   - {o['label']}" + (f": {o['description']}" if o["description"] else ""))
    return "\n".join(lines)


def answer_prompt(data: dict, reply: str) -> str:
    """What the resumed worker receives: its own questions + the user's answers (verbatim)."""
    qs = "\n".join(f"- [{q['header']}] {q['question']}" for q in data["questions"])
    return ("The user answered your questions.\n\nYOUR QUESTIONS:\n" + qs
            + "\n\nUSER'S ANSWERS (verbatim):\n" + reply.strip()
            + "\n\nContinue the task with these answers. Same final-message format as before.")


def from_planner(questions: list) -> dict | None:
    """Astra PLAN_SCHEMA questions (structured items; legacy plain strings tolerated)."""
    return normalize({"questions": questions or []})
