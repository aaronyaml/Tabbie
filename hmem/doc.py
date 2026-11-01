"""Memory documents: Markdown body + a small frontmatter block.

Frontmatter is `key: value` lines. Scalars are written bare; lists/dicts/numbers/bools as JSON so
they round-trip without a YAML dependency and stay readable/greppable:

    ---
    id: projects/acme/auth-ownership
    scope: project
    tags: ["supabase", "auth"]
    confidence: 0.9
    ---
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re

FIELDS = ("id", "scope", "project", "agent", "title", "description", "type", "status", "version",
          "created_at", "updated_at", "created_by", "approved_by", "source_sessions", "evidence",
          "trust", "taint", "confidence", "supersedes", "superseded_by", "tags", "last_validated", "refs",
          "mirror_of", "inject_after")
SCOPES = ("global", "project", "agent", "session")
STATUSES = ("active", "deprecated", "superseded", "archived")
TYPES = ("fact", "preference", "rule", "procedure", "decision", "reference", "lesson")
TRUST = ("external", "agent", "user")  # ascending; nothing ever raises trust
LIST_FIELDS = ("source_sessions", "evidence", "supersedes", "tags", "refs")
MAX_BODY = 4000

_FM = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.S)
_SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:60]
    return s or "note"


def valid_slug(s: str) -> bool:
    return bool(s and _SLUG.match(s))


def trust_min(*levels: str) -> str:
    ranks = [TRUST.index(x) if x in TRUST else 0 for x in levels if x] or [0]
    return TRUST[min(ranks)]


def _val(raw: str):
    raw = raw.strip()
    if raw == "":
        return None
    if raw[0] in "[{\"" or raw in ("true", "false", "null") or re.fullmatch(r"-?\d+(\.\d+)?", raw):
        try:
            return json.loads(raw)
        except ValueError:
            pass
    return raw


def parse(text: str) -> tuple[dict, str]:
    m = _FM.match(text)
    if not m:
        return {}, text.strip()
    meta = {}
    for line in m.group(1).splitlines():
        if ":" not in line or line.startswith((" ", "\t", "#")):
            continue
        k, _, v = line.partition(":")
        meta[k.strip()] = _val(v)
    for f in LIST_FIELDS:
        if f in meta and meta[f] is not None and not isinstance(meta[f], list):
            meta[f] = [meta[f]]
    return meta, m.group(2).strip()


def _fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, str) and "\n" not in v and not (v[:1] in "[{\"" or v in ("true", "false", "null")
                                                    or re.fullmatch(r"-?\d+(\.\d+)?", v)):
        return v
    return json.dumps(v, ensure_ascii=False)


def render(meta: dict, body: str) -> str:
    keys = [k for k in FIELDS if k in meta] + sorted(k for k in meta if k not in FIELDS)
    lines = [f"{k}: {_fmt(meta[k])}".rstrip() for k in keys]
    return "---\n" + "\n".join(lines) + "\n---\n\n" + body.strip() + "\n"


def validate(meta: dict, body: str) -> list[str]:
    """Schema problems (empty list = valid)."""
    errs = []
    for f in ("id", "scope", "title", "status", "created_by"):
        if not meta.get(f):
            errs.append(f"missing {f}")
    if meta.get("scope") not in SCOPES:
        errs.append(f"bad scope {meta.get('scope')!r}")
    if meta.get("status") not in STATUSES:
        errs.append(f"bad status {meta.get('status')!r}")
    if meta.get("type") and meta["type"] not in TYPES:
        errs.append(f"bad type {meta['type']!r}")
    if meta.get("trust") and meta["trust"] not in TRUST:
        errs.append(f"bad trust {meta['trust']!r}")
    if meta.get("scope") == "project" and not valid_slug(str(meta.get("project") or "")):
        errs.append("project scope needs a project slug")
    if meta.get("scope") == "agent" and not valid_slug(str(meta.get("agent") or "")):
        errs.append("agent scope needs an agent slug")
    if meta.get("trust") == "external":  # untrusted origin: may describe, never instruct
        if meta.get("type") not in ("fact", "reference"):
            errs.append("external-trust memory may only be type fact/reference")
        if meta.get("scope") == "global":
            errs.append("external-trust memory cannot be global")
    c = meta.get("confidence")
    if c is not None and not (isinstance(c, (int, float)) and 0 <= c <= 1):
        errs.append("confidence must be 0..1")
    if len(body) > MAX_BODY:
        errs.append(f"body too long ({len(body)} > {MAX_BODY}); split it, one concept per memory")
    if not body.strip():
        errs.append("empty body")
    return errs
