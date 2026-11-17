"""Approve or reject memory proposals by asking in chat ("approve memory proposal p-20261117-6fef86").

Workers can't approve memories (hmem/cli.py refuses them): a worker can be steered by a repo file or a web
page, so it may only propose. This is different: it reads the USER'S OWN message, before any AI sees it, and
acts on the proposal ids the user typed. Text from a page, a file or a worker's reply never reaches here, so it
can't approve anything. Approvals land as decided_by "user", same as the WebUI or `hmem approve`.

  approve p-20261117-6fef86            approve memory proposal p-…, p-…      reject p-… (reason optional after it)
  approve the memory proposal          (only when exactly one is pending)    approve p-… anyway   (flagged ones)
"""
from __future__ import annotations

import re

PID = re.compile(r"\bp-\d{8}-[0-9a-f]{4,}\b", re.I)
VERB = re.compile(r"^\s*(?:please\s+|pls\s+|can you\s+|could you\s+)?(approve|accept|ok|reject|decline|discard)\b", re.I)
ABOUT = re.compile(r"\b(memor(?:y|ies)|proposals?)\b", re.I)
ANYWAY = re.compile(r"\b(anyway|confirm(?:ed)?|i'?m sure|regardless)\b", re.I)


def match(text: str) -> tuple[str, list[str], bool] | None:
    """(verb, ids, confirm) when this message asks to approve/reject memory proposals, else None."""
    t = (text or "").strip()
    if len(t) > 400:
        return None
    m = VERB.match(t)
    if not m:
        return None
    ids = list(dict.fromkeys(x.lower() for x in PID.findall(t)))
    if not ids and not ABOUT.search(t):
        return None
    verb = "reject" if m.group(1).lower() in ("reject", "decline", "discard") else "approve"
    return verb, ids, bool(ANYWAY.search(t))


def handle(text: str) -> str | None:
    """A line for the hub to relay, or None when the message isn't a proposal decision."""
    got = match(text)
    if not got:
        return None
    verb, ids, confirm = got
    from hmem import proposals as P
    pending = P.list_("pending")
    if not ids:
        if len(pending) != 1:
            return (f"There are {len(pending)} pending memory proposals, so say which: e.g. \"{verb} "
                    f"{pending[0]['id'] if pending else 'p-…'}\"." if pending else "There are no pending memory proposals.")
        ids = [pending[0]["id"]]
    reason = ""
    if verb == "reject":
        reason = PID.sub("", VERB.sub("", text, count=1)).strip(" :,-.")[:200]
    out = []
    for pid in ids[:20]:
        try:
            if verb == "approve":
                why = P.needs_confirmation(P.load(pid)[0]) if P.load(pid) and P.load(pid)[1] == "pending" else []
                if why and not confirm:
                    out.append(f"{pid}: not approved yet, it needs a second look ({'; '.join(why)}). "
                               f"Check it with `hmem show {pid}`, then say \"approve {pid} anyway\".")
                    continue
                p = P.approve(pid, actor="user", confirm_flagged=confirm)
                title = ((p.get("proposed_change") or {}).get("meta") or {}).get("title") or p.get("target") or ""
                out.append(f"{pid}: approved" + (f" ({title})" if title else "") + ".")
            else:
                P.reject(pid, actor="user", reason=reason)
                out.append(f"{pid}: rejected.")
        except Exception as e:  # noqa: BLE001  (ProposalError, PermissionDenied, store errors: say why)
            out.append(f"{pid}: couldn't {verb} it ({e}).")
    return " ".join(out)
