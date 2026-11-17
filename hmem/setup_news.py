"""Setup steps added after people installed Tabbie: offered once, after the update that brings them.

Every step `bin/setup` gained after the first release goes in STEPS with the date it arrived. A step is
"seen" once this Mac has been through it in setup (the setup page or bin/setup-terminal marks it, done or
skipped), or once someone answers the offer in the WebUI ("Set up" or "Not now"). The WebUI asks the sidecar
for steps that are neither seen nor already done, and shows them as a small card (webui-extension/setup-news.js).
Optional steps stay optional: the card always has "Not now", and it never comes back after that.

State: state/setup-seen.json, {"seen": [ids]}. Per machine, git-ignored with the rest of state/.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEEN = ROOT / "state" / "setup-seen.json"

# Newest last. `open` is where the WebUI's "Set up" goes: a Settings section (settings:<id>) or a Terminal
# command shown to the user. `later` is what "Not now" says: where to find the step again. `done` names a check in the caller (sidecar) that says it's already set up.
STEPS = [
    {"id": "secrets", "title": "Secrets (1Password)", "added": "2026-11-17", "optional": True,
     "blurb": "Keep API keys (Resend, Supabase, Stripe…) in 1Password, so Claude Code and Codex can use them "
              "while they work without anyone seeing them. Needs a 1Password account.",
     "open": "settings:secrets", "terminal": "bin/setup --only secrets",
     "later": "No problem. Set up 1Password any time in Settings → Accounts → Secrets."},
]
IDS = [s["id"] for s in STEPS]


def seen() -> set[str]:
    try:
        return set(json.loads(SEEN.read_text(encoding="utf-8")).get("seen") or [])
    except (OSError, ValueError, AttributeError):
        return set()


def mark(ids) -> None:
    """Mark steps seen (unknown ids are ignored). Atomic write."""
    now = seen() | {i for i in ids if i in IDS}
    SEEN.parent.mkdir(parents=True, exist_ok=True)
    tmp = SEEN.with_suffix(".tmp")
    tmp.write_text(json.dumps({"seen": sorted(now)}, indent=2), encoding="utf-8")
    os.replace(tmp, SEEN)


def pending(done: dict[str, bool] | None = None) -> list[dict]:
    """Steps to offer: not seen here and not already set up some other way."""
    s, done = seen(), done or {}
    return [dict(x) for x in STEPS if x["id"] not in s and not done.get(x["id"])]


if __name__ == "__main__":  # bin/setup-terminal: python3 -m hmem.setup_news mark <id>…
    import sys
    if sys.argv[1:2] == ["mark"]:
        mark(sys.argv[2:])
