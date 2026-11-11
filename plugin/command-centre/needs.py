"""What Tabbie needs from the user and doesn't have yet, asked once in the app.

Setup asks for these too, but an install from before a need existed (or a skipped answer) would never be
asked again. The WebUI (needs.js) asks on load for anything listed here that is missing: after setup,
after an update, any time. To make Tabbie ask for something new, add one entry to NEEDS.

    missing()          [{key, title, hint, label, placeholder, autocomplete, max}] still to answer
    save(key, value)   validate and store one answer; raises ValueError on a bad value
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCAL_JS = ROOT / "webui-extension" / "local.js"


def _local() -> dict:
    """webui-extension/local.js (written by bin/setup) as a dict of its simple "key: value" lines."""
    out = {}
    try:
        text = LOCAL_JS.read_text(encoding="utf-8")
    except OSError:
        return out
    for m in re.finditer(r'^\s*([A-Za-z_]\w*):\s*("(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'),?\s*(?://.*)?$', text, re.M):
        raw = m.group(2)
        try:
            out[m.group(1)] = json.loads(raw) if raw.startswith('"') else raw[1:-1]
        except ValueError:
            out[m.group(1)] = raw[1:-1]
    return out


def _write_local(values: dict) -> None:
    home = str(Path.home())
    cur = {"home": home, "hermes": str(ROOT), "motion": str(Path(home) / "motion"), "name": "", **_local(), **values}
    lines = ["// Per-machine settings (git-ignored). Regenerate with bin/setup.", "window.HX_LOCAL = {"]
    lines += [f"  {k}: {json.dumps(str(v), ensure_ascii=False)}," for k, v in cur.items()]
    lines.append("};")
    tmp = LOCAL_JS.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(LOCAL_JS)


def _clean_name(v: str) -> str:
    v = " ".join(str(v or "").split())[:40]
    if not v or not re.search(r"\w", v):
        raise ValueError("Type your first name")
    return v.split(" ")[0] if len(v.split()) > 1 and len(v) > 24 else v


NEEDS = [
    {"key": "name", "title": "What should Tabbie call you?", "hint": "Your first name, for greetings like “Evening, Sam”.",
     "label": "First name", "placeholder": "Sam", "autocomplete": "given-name", "max": 40,
     "have": lambda: bool((_local().get("name") or "").strip()),
     "save": lambda v: _write_local({"name": _clean_name(v)})},
]


def missing() -> list[dict]:
    return [{k: v for k, v in n.items() if k not in ("have", "save")} for n in NEEDS if not n["have"]()]


def save(key: str, value: str) -> None:
    for n in NEEDS:
        if n["key"] == key:
            n["save"](value)
            return
    raise ValueError("unknown setting")
