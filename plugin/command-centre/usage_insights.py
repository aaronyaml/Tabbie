"""Tabbie's Insights page: every model token on this Mac (Claude Code, Codex and the Hermes hub) with its
API-equivalent cost, for today / 7 / 30 / 90 days, by day, weekday, hour, model, project and chat.

Reads what is already on disk (nothing here calls a model or needs a key):
  ~/.claude/projects/**/*.jsonl   Claude Code transcripts. Each assistant message carries `usage`
                                  (input, output, cache read, cache write 5m / 1h) and `model`. One message
                                  is written as several lines (one per content block) with the same
                                  message.id, and resumed sessions copy earlier messages into a new file,
                                  so messages are counted once by id across all files.
  ~/.codex/sessions/**/*.jsonl    Codex rollouts. `token_count` events carry running totals
                                  (input includes cached input); the model comes from `turn_context`.
  ~/.hermes/state.db              The hub (Hermes agent): per session and model, tokens and API calls
                                  (session_model_usage). Its tokens are spread over the session's
                                  assistant messages so the day / hour charts place them when they ran.
Covers every Claude Code / Codex run on this Mac: Tabbie workers and the user's own terminal sessions.

Cost is API-equivalent: tokens x the providers' published list prices (RATES, one table), even though
the work ran on subscriptions. Long-context rates apply per request where a request's prompt size is
known (Claude messages, Codex turns), and per session average for the hub.

Speed: only files written in the last HORIZON_DAYS are read; each file's parse is cached (in memory and
in state/usage-insights.json) with its size/mtime and byte offset, so a grown transcript is read from
where it stopped and an unchanged one not at all. A cold full scan runs in a background thread.

    payload(wait=6.0)  the Insights page's data (sidecar GET /usage)
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import sqlite3
import tempfile
import threading
import time
from pathlib import Path

from . import model_latest

HOME = Path(__file__).resolve().parents[2]  # ~/hermes
STATE = HOME / "state"
CACHE_FILE = STATE / "usage-insights.json"
CLAUDE_DIR = Path.home() / ".claude" / "projects"
CODEX_DIR = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "sessions"
HUB_DB: Path | None = None  # None: the Hermes home's state.db (hmem.paths)
HORIZON_DAYS = 92        # files untouched for longer can't hold anything in the 90-day window
FRESH_S = 60             # reuse the last scan for this long
CACHE_VERSION = 2        # 2: hour of day + long-context flag per request
WINDOWS = {"today": 1, "7d": 7, "30d": 30, "90d": 90}
SOURCES = ("claude", "codex", "hub")

# ---------------------------------------------------------------------------------------------------------
# API list prices, US$ per million tokens: (input, output, cache read, cache write short, cache write long).
# "input" is uncached input. Cache write short/long = Anthropic 5-minute / 1-hour cache, OpenAI 30-minute /
# 12-hour cache (Codex rollouts don't report cache writes, so OpenAI writes are never charged here).
# Sources, read 2026-11-11:
#   Anthropic  https://platform.claude.com/docs/en/about-claude/pricing (Model pricing + Long context)
#   OpenAI     https://developers.openai.com/api/docs/pricing (Standard tier)
# A model id matches a row when it equals the key (after dropping a -YYYYMMDD date) or is the key plus a
# variant suffix ("-fast", not "-7"); the longest key wins. A newer version of a known family (e.g. claude-opus-5-7) is priced as that family's
# newest listed model and flagged as an estimate; anything else has no price and is reported as such.
RATES: dict[str, tuple[float, float, float, float, float]] = {
    # Anthropic
    "claude-fable-5-2": (8.00, 40.00, 0.20, 10.00, 16.00),
    "claude-fable-5-1": (10.00, 50.00, 0.25, 12.50, 20.00),
    "claude-fable-5": (10.00, 50.00, 1.00, 12.50, 20.00),
    "claude-mythos-5-2": (8.00, 40.00, 0.20, 10.00, 16.00),
    "claude-mythos-5-1": (10.00, 50.00, 0.25, 12.50, 20.00),
    "claude-mythos-5": (10.00, 50.00, 1.00, 12.50, 20.00),
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00, 8.00),
    "claude-opus-5": (5.00, 25.00, 0.50, 6.25, 10.00),
    "claude-opus-4-8": (5.00, 25.00, 0.50, 6.25, 10.00),
    "claude-opus-4-7": (5.00, 25.00, 0.50, 6.25, 10.00),
    "claude-opus-4-6": (5.00, 25.00, 0.50, 6.25, 10.00),
    "claude-opus-4-5": (5.00, 25.00, 0.50, 6.25, 10.00),
    "claude-sonnet-5-5": (2.00, 10.00, 0.10, 2.50, 4.00),
    "claude-sonnet-5": (2.00, 10.00, 0.20, 2.50, 4.00),
    "claude-sonnet-4-6": (3.00, 15.00, 0.30, 3.75, 6.00),
    "claude-sonnet-4-5": (3.00, 15.00, 0.30, 3.75, 6.00),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125, 0.20),
    "claude-haiku-4-5": (1.00, 5.00, 0.10, 1.25, 2.00),
    # OpenAI
    "gpt-6-astra": (10.00, 50.00, 1.00, 12.50, 25.00),
    "gpt-6.1-sol": (2.00, 10.00, 0.10, 2.50, 5.00),
    "gpt-6.1-terra": (1.00, 5.00, 0.05, 1.25, 2.50),
    "gpt-6-sol": (2.00, 10.00, 0.20, 2.50, 5.00),
    "gpt-6-luna": (0.10, 0.50, 0.01, 0.125, 0.25),
    "gpt-5.6-sol": (4.00, 20.00, 0.40, 5.00, 10.00),
    "gpt-5.6-terra": (2.00, 12.00, 0.20, 2.50, 5.00),
    "gpt-5.6-luna": (0.20, 1.20, 0.02, 0.25, 0.50),
    "gpt-5.5": (5.00, 30.00, 0.50, 0.0, 0.0),
    "gpt-5.4": (2.50, 15.00, 0.25, 0.0, 0.0),
    "gpt-5.4-mini": (0.75, 4.50, 0.075, 0.0, 0.0),
    "gpt-5.4-nano": (0.20, 1.25, 0.02, 0.0, 0.0),
    "gpt-5.3-codex": (1.75, 14.00, 0.175, 0.0, 0.0),
    "gpt-5.2": (1.75, 14.00, 0.175, 0.0, 0.0),
    "gpt-5.1": (1.25, 10.00, 0.125, 0.0, 0.0),
    "gpt-5": (1.25, 10.00, 0.125, 0.0, 0.0),
    "gpt-5-mini": (0.25, 2.00, 0.025, 0.0, 0.0),
    "gpt-5-nano": (0.05, 0.40, 0.005, 0.0, 0.0),
}
# Long-context rows: a request whose prompt (input + cache read + cache write) is over the threshold.
LONG: dict[str, tuple[int, tuple[float, float, float, float, float]]] = {
    "claude-haiku-5-5": (100_000, (0.50, 2.50, 0.05, 0.625, 1.00)),
    "gpt-6-astra": (272_000, (20.00, 75.00, 2.00, 25.00, 50.00)),
    "gpt-6.1-sol": (272_000, (4.00, 15.00, 0.20, 5.00, 10.00)),
    "gpt-6.1-terra": (272_000, (2.00, 7.50, 0.10, 2.50, 5.00)),
    "gpt-6-sol": (272_000, (4.00, 15.00, 0.40, 5.00, 10.00)),
    "gpt-6-luna": (272_000, (0.20, 0.75, 0.02, 0.25, 0.50)),
    "gpt-5.6-sol": (272_000, (8.00, 30.00, 0.80, 10.00, 20.00)),
    "gpt-5.6-terra": (272_000, (4.00, 18.00, 0.40, 5.00, 10.00)),
    "gpt-5.6-luna": (272_000, (0.40, 1.80, 0.04, 0.50, 1.00)),
    "gpt-5.5": (272_000, (10.00, 45.00, 1.00, 0.0, 0.0)),
    "gpt-5.4": (272_000, (5.00, 22.50, 0.50, 0.0, 0.0)),
}
PRICES_CHECKED = "2026-11-11"
_DATE_SUFFIX = re.compile(r"-\d{8}$")


def rate_key(model: str) -> tuple[str | None, bool]:
    """(RATES key, is_estimate) for a model id; (None, False) when it can't be priced."""
    m = _DATE_SUFFIX.sub("", (model or "").strip().lower())
    best = None
    for k in RATES:
        # "-<digit>" after a key is a newer version (claude-opus-5-7 is not claude-opus-5), not a variant
        if (m == k or (m.startswith(k + "-") and not m[len(k) + 1:][:1].isdigit())) and (best is None or len(k) > len(best)):
            best = k
    if best:
        return best, False
    p = model_latest.parse(m)
    if p:  # a newer version of a known family: that family's newest listed price
        same = [(q[2], k) for k in RATES if (q := model_latest.parse(k)) and q[:2] == p[:2]]
        if same:
            return max(same)[1], True
    return None, False


def price_for(model: str, long: bool = False) -> tuple[tuple[float, ...] | None, bool]:
    """(price row or None, is_estimate) for a model id; `long` picks the long-context row where there is one."""
    k, est = rate_key(model)
    if k is None:
        return None, False
    if long and k in LONG:
        return LONG[k][1], est
    return RATES[k], est


def is_long(model: str, prompt_tokens: int) -> bool:
    k, _ = rate_key(model)
    return bool(k in LONG and prompt_tokens > LONG[k][0])


def cost_of(model: str, t: dict, long: bool = False) -> float | None:
    """API-equivalent US$ for one token bucket {in, out, cr, cw5, cw1}; None when the model has no price."""
    row, _ = price_for(model, long)
    if row is None:
        return None
    pi, po, pcr, pc5, pc1 = row
    return (t.get("in", 0) * pi + t.get("out", 0) * po + t.get("cr", 0) * pcr
            + t.get("cw5", 0) * pc5 + t.get("cw1", 0) * pc1) / 1e6


def cost_parts(model: str, t: dict, long: bool = False) -> dict | None:
    """The same cost split by token kind: {"in", "out", "cr", "cw"} (cw = both cache-write tiers)."""
    row, _ = price_for(model, long)
    if row is None:
        return None
    pi, po, pcr, pc5, pc1 = row
    return {"in": t.get("in", 0) * pi / 1e6, "out": t.get("out", 0) * po / 1e6, "cr": t.get("cr", 0) * pcr / 1e6,
            "cw": (t.get("cw5", 0) * pc5 + t.get("cw1", 0) * pc1) / 1e6}


def friendly(model: str) -> str:
    name = model_latest.display_name(model)
    if name != model:
        return name
    parts = model.split("-")
    if model.startswith("claude-") and len(parts) >= 3 and parts[2].isdigit():  # claude-opus-5: "Opus 5"
        return f"{parts[1].capitalize()} {'.'.join(parts[2:])}"
    if model.startswith("gpt-"):  # gpt-6-astra: "GPT-6 Astra", gpt-5.4-mini: "GPT-5.4 Mini"
        return "GPT-" + " ".join([parts[1]] + [p.capitalize() for p in parts[2:]])
    return name


# ---------------------------------------------------------------------------------------------------------
# Parsing (pure functions over lines; the scanner feeds them new bytes only)

def _when(ts) -> tuple[str, int] | None:
    """ISO UTC timestamp (or epoch seconds) -> (local day YYYY-MM-DD, local hour)."""
    try:
        if isinstance(ts, (int, float)):
            d = _dt.datetime.fromtimestamp(ts).astimezone()
        else:
            d = _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if d.tzinfo is None:
                d = d.replace(tzinfo=_dt.timezone.utc)
            d = d.astimezone()
    except (TypeError, ValueError, AttributeError, OverflowError, OSError):
        return None
    return d.strftime("%Y-%m-%d"), d.hour


def parse_claude_lines(lines, entry: dict) -> None:
    """Fold Claude Code transcript lines into entry
    {"sid", "cwd", "msgs": {id: [day, model, in, out, cr, cw5, cw1, hour]}}."""
    msgs = entry.setdefault("msgs", {})
    for raw in lines:
        if b'"usage"' not in raw:
            if not entry.get("cwd") and b'"cwd"' in raw:
                try:
                    d = json.loads(raw)
                    entry["cwd"] = d.get("cwd") or ""
                    entry["sid"] = entry.get("sid") or d.get("sessionId") or ""
                except ValueError:
                    pass
            continue
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        m = d.get("message")
        if not isinstance(m, dict):
            continue
        u, model = m.get("usage"), m.get("model") or ""
        if not isinstance(u, dict) or not model or model.startswith("<"):  # "<synthetic>": local stub, no tokens
            continue
        w = _when(d.get("timestamp"))
        if not w:
            continue
        if not entry.get("cwd"):
            entry["cwd"] = d.get("cwd") or ""
        if not entry.get("sid"):
            entry["sid"] = d.get("sessionId") or ""
        cc = u.get("cache_creation") if isinstance(u.get("cache_creation"), dict) else None
        cw_total = int(u.get("cache_creation_input_tokens") or 0)
        if cc is not None:
            cw1 = int(cc.get("ephemeral_1h_input_tokens") or 0)
            cw5 = int(cc.get("ephemeral_5m_input_tokens") or 0)
            if cw1 + cw5 < cw_total:
                cw5 += cw_total - cw1 - cw5
        else:
            cw1, cw5 = 0, cw_total
        mid = m.get("id") or d.get("requestId") or d.get("uuid")
        msgs[mid] = [w[0], model, int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0),
                     int(u.get("cache_read_input_tokens") or 0), cw5, cw1, w[1]]


def parse_codex_lines(lines, entry: dict) -> None:
    """Fold Codex rollout lines into entry {"sid", "cwd", "model", "prev": [in, cached, out],
    "rows": {"day|hour|model|long": [in, out, cr]}}. Token counts come from deltas of the running total, so a
    repeated event never counts twice; a turn whose prompt is past the long-context threshold is flagged."""
    rows = entry.setdefault("rows", {})
    prev = entry.setdefault("prev", [0, 0, 0])
    for raw in lines:
        if b'"session_meta"' in raw or b'"turn_context"' in raw:
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            p = d.get("payload") or {}
            if d.get("type") == "session_meta":
                entry["sid"] = p.get("id") or p.get("session_id") or entry.get("sid", "")
                entry["cwd"] = p.get("cwd") or entry.get("cwd", "")
            elif d.get("type") == "turn_context" and p.get("model"):
                entry["model"] = p["model"]
            continue
        if b'"token_count"' not in raw:
            continue
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        info = ((d.get("payload") or {}).get("info") or {})
        tot = info.get("total_token_usage")
        if not isinstance(tot, dict):
            continue
        cur = [int(tot.get("input_tokens") or 0), int(tot.get("cached_input_tokens") or 0),
               int(tot.get("output_tokens") or 0)]
        last = info.get("last_token_usage") or {}
        if cur[0] < prev[0] or cur[2] < prev[2]:  # totals restarted: count this event's own usage
            delta = [int(last.get("input_tokens") or 0), int(last.get("cached_input_tokens") or 0),
                     int(last.get("output_tokens") or 0)]
        else:
            delta = [cur[i] - prev[i] for i in range(3)]
        prev[:] = cur
        if not any(delta):
            continue
        w = _when(d.get("timestamp"))
        if not w:
            continue
        model = entry.get("model") or "codex"
        prompt = int(last.get("input_tokens") or 0) or delta[0]  # one request's prompt (input includes cached)
        key = f"{w[0]}|{w[1]}|{model}|{1 if is_long(model, prompt) else 0}"
        r = rows.setdefault(key, [0, 0, 0])
        r[0] += max(0, delta[0] - delta[1])   # uncached input
        r[1] += max(0, delta[2])              # output (includes reasoning)
        r[2] += max(0, delta[1])              # cached input


# ---------------------------------------------------------------------------------------------------------
# Incremental scanner

_lock = threading.Lock()
_mem: dict = {"files": None, "scanned_at": 0.0, "result": None, "result_at": 0.0, "thread": None}


def _load_cache() -> dict:
    if _mem["files"] is not None:
        return _mem["files"]
    try:
        d = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        files = d.get("files") if d.get("v") == CACHE_VERSION else None
    except (OSError, ValueError):
        files = None
    _mem["files"] = files if isinstance(files, dict) else {}
    return _mem["files"]


def _save_cache(files: dict) -> None:
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"v": CACHE_VERSION, "files": files}, separators=(",", ":")), encoding="utf-8")
        tmp.replace(CACHE_FILE)
    except OSError:
        pass


def _read_new(path: Path, entry: dict, parse) -> None:
    """Parse the bytes after entry["off"] up to the last complete line; a shrunk file starts over."""
    st = path.stat()
    if st.st_size < entry.get("off", 0):
        for k in ("msgs", "rows", "prev", "model", "sid", "cwd"):
            entry.pop(k, None)
        entry["off"] = 0
    off = entry.get("off", 0)
    with open(path, "rb") as fh:
        fh.seek(off)
        data = fh.read()
    end = data.rfind(b"\n")
    if end >= 0:
        parse(data[: end + 1].splitlines(), entry)
        entry["off"] = off + end + 1
    entry["size"], entry["mtime"] = st.st_size, st.st_mtime


def _walk(root: Path, cutoff: float):
    if not root.exists():
        return
    for dirpath, _dirs, names in os.walk(root):
        for n in names:
            if n.endswith(".jsonl"):
                p = Path(dirpath) / n
                try:
                    mt = p.stat().st_mtime
                except OSError:
                    continue
                if mt >= cutoff:
                    yield p


def scan(claude_dir: Path | None = None, codex_dir: Path | None = None, files: dict | None = None,
         now: float | None = None) -> dict:
    """Bring the per-file cache up to date; returns it. Files gone or past the horizon are dropped."""
    claude_dir = claude_dir or CLAUDE_DIR
    codex_dir = codex_dir or CODEX_DIR
    files = _load_cache() if files is None else files
    cutoff = (now or time.time()) - HORIZON_DAYS * 86400
    min_day = _dt.date.fromtimestamp(cutoff).strftime("%Y-%m-%d")
    seen = set()
    for kind, root, parse in (("claude", claude_dir, parse_claude_lines), ("codex", codex_dir, parse_codex_lines)):
        for p in _walk(root, cutoff):
            key = str(p)
            seen.add(key)
            e = files.get(key)
            try:
                st = p.stat()
            except OSError:
                continue
            if e and e.get("size") == st.st_size and e.get("mtime") == st.st_mtime:
                continue
            if e is None:
                e = files[key] = {"kind": kind, "off": 0}
            try:
                _read_new(p, e, parse)
            except OSError:
                continue
            if kind == "claude":  # keep the cache small: forget messages older than the window
                e["msgs"] = {k: v for k, v in e.get("msgs", {}).items() if v[0] >= min_day}
    for key in [k for k in files if k not in seen]:
        files.pop(key, None)
    return files


# ---------------------------------------------------------------------------------------------------------
# The hub (Hermes agent): state.db, read-only

def _hub_db() -> Path:
    if HUB_DB is not None:
        return HUB_DB
    try:
        from hmem.paths import hermes_home
        return hermes_home() / "state.db"
    except Exception:
        return Path.home() / ".hermes" / "state.db"


def hub_rows(db: Path | None = None, since: float | None = None) -> list[tuple]:
    """[(day, hour, model, cwd, chat key, long, in, out, cr, cw)] for hub API calls since `since`.
    A session's tokens per model are split evenly over its assistant messages in [first_seen, last_seen]
    (each one an API call); a session without any (titles, background review) lands on last_seen."""
    db = db or _hub_db()
    since = since if since is not None else time.time() - HORIZON_DAYS * 86400
    if not db.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
    except sqlite3.Error:
        return []
    out: list[tuple] = []
    try:
        conn.row_factory = sqlite3.Row
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "session_model_usage" not in tables or "sessions" not in tables:
            return []
        sess = {r["id"]: r for r in conn.execute(
            "SELECT id, source, title, cwd FROM sessions WHERE COALESCE(last_activity_at, ended_at, started_at, 0) >= ?",
            (since - 86400,))}
        stamps: dict[str, list[float]] = {}
        if "messages" in tables:
            for r in conn.execute("SELECT session_id, timestamp FROM messages WHERE role='assistant' AND timestamp >= ?",
                                  (since - 86400,)):
                stamps.setdefault(r[0], []).append(float(r[1] or 0))
        for u in conn.execute(
                "SELECT session_id, model, task, api_call_count, input_tokens, output_tokens, cache_read_tokens, "
                "cache_write_tokens, first_seen, last_seen FROM session_model_usage WHERE COALESCE(last_seen, 0) >= ?",
                (since,)):
            sid, model = u["session_id"], u["model"] or "unknown"
            tin, tout = int(u["input_tokens"] or 0), int(u["output_tokens"] or 0)
            cr, cw = int(u["cache_read_tokens"] or 0), int(u["cache_write_tokens"] or 0)
            if not (tin or tout or cr or cw):
                continue
            calls = max(1, int(u["api_call_count"] or 0))
            long = is_long(model, (tin + cr + cw) // calls)
            s = sess.get(sid)
            title = (s["title"] if s else None) or ""
            src = (s["source"] if s else "") or ""
            if src == "webui":
                chat = f"chat:{title or 'Untitled chat'}"
            else:
                chat = f"hub:{title or ('Hermes CLI' if src == 'cli' else 'Background tasks')}"
            cwd = (s["cwd"] if s else "") or ""
            lo, hi = float(u["first_seen"] or 0) - 1, float(u["last_seen"] or 0) + 1
            ts = [t for t in stamps.get(sid, []) if lo <= t <= hi] if not u["task"] else []
            if not ts:
                ts = [float(u["last_seen"] or u["first_seen"] or 0)]
            n = len(ts)
            parts = [(tin // n, tout // n, cr // n, cw // n)] * n
            # put the integer remainders on the last call so totals stay exact
            parts[-1] = (tin - tin // n * (n - 1), tout - tout // n * (n - 1), cr - cr // n * (n - 1), cw - cw // n * (n - 1))
            for t, (a, b, c, d) in zip(ts, parts):
                w = _when(t)
                if w:
                    out.append((w[0], w[1], model, cwd, chat, long, a, b, c, d))
    except sqlite3.Error:
        return out
    finally:
        conn.close()
    return out


# ---------------------------------------------------------------------------------------------------------
# Tabbie chats: which Claude Code session / Codex thread belongs to which WebUI chat

_chat_mem: dict = {"dirs": {}, "titles": {}}


def chat_map(requests_dir: Path | None = None, sessions_dir: Path | None = None) -> dict:
    """{executor session/thread id: chat title} from Tabbie's request records (cached per request dir)."""
    requests_dir = requests_dir or (STATE / "requests")
    if sessions_dir is None:
        try:
            from hmem.paths import webui_state
            sessions_dir = webui_state() / "sessions"
        except Exception:
            sessions_dir = Path("/nonexistent")
    since = (_dt.date.today() - _dt.timedelta(days=HORIZON_DAYS + 1)).strftime("%Y%m%d")
    dirs = _chat_mem["dirs"]
    try:
        names = [n for n in os.listdir(requests_dir) if n[:8] >= since]
    except OSError:
        names = []
    for n in names:
        if n in dirs and dirs[n][0]:
            continue
        try:
            r = json.loads((requests_dir / n / "request.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        ex = r.get("executor") or {}
        dirs[n] = (ex.get("session_id") or ex.get("thread_id") or "", r.get("session_id") or "")
    out = {}
    titles = _chat_mem["titles"]
    for n in names:
        xid, sid = dirs.get(n, ("", ""))
        if not xid or not sid:
            continue
        if sid not in titles:
            try:
                s = json.loads((Path(sessions_dir) / f"{sid}.json").read_text(encoding="utf-8"))
                titles[sid] = str(s.get("title") or "Untitled chat")[:80]
            except (OSError, ValueError):
                titles[sid] = "Tabbie chat"
        out[xid] = titles[sid]
    return out


# ---------------------------------------------------------------------------------------------------------
# Aggregation

TOK = ("in", "out", "cr", "cw5", "cw1")


def _zero() -> dict:
    return {k: 0 for k in TOK}


def _project(cwd: str) -> str:
    if not cwd:
        return "Unknown folder"
    p = Path(cwd)
    norm = os.path.normcase  # Windows paths compare without case
    if norm(str(p)) == norm(str(Path.home())):
        return "Home folder"
    temps = ("/tmp", "/private/tmp", "/var/folders", tempfile.gettempdir())  # %TEMP% on Windows
    if any(norm(str(p)).startswith(norm(t)) for t in temps if t):
        return "Temporary folder"
    return p.name or cwd


def _chat_label(k: str) -> str:
    kind, _, rest = k.partition(":")
    return {"chat": rest, "cli": f"{rest} · Claude Code, outside Tabbie", "codex": f"{rest} · Codex, outside Tabbie",
            "hub": f"{rest} · hub"}.get(kind, rest)


def _sum(t: dict) -> int:
    return sum(t[k] for k in TOK)


def aggregate(files: dict, chats: dict | None = None, today: _dt.date | None = None, hub: list | None = None) -> dict:
    today = today or _dt.date.today()
    chats = chats or {}
    span = max(WINDOWS.values())
    days = [(today - _dt.timedelta(days=i)).strftime("%Y-%m-%d") for i in range(span - 1, -1, -1)]
    win_start = {w: days[-n] for w, n in WINDOWS.items()}

    # one row per (day, hour, model, project, chat, source, long): Claude messages deduped by id across files
    rows: dict[tuple, dict] = {}
    seen: set = set()

    def add(day, hour, model, project, chat, source, long, tin, tout, cr, cw5, cw1):
        if day < days[0] or day > days[-1]:
            return
        r = rows.setdefault((day, int(hour), model, project, chat, source, bool(long)), _zero())
        r["in"] += tin; r["out"] += tout; r["cr"] += cr; r["cw5"] += cw5; r["cw1"] += cw1

    for key in sorted(files, key=lambda k: files[k].get("mtime", 0)):
        e = files[key]
        proj = _project(e.get("cwd", ""))
        xid = e.get("sid", "")
        if e.get("kind") == "claude":
            chat = f"chat:{chats[xid]}" if xid in chats else f"cli:{proj}"
            for mid, v in (e.get("msgs") or {}).items():
                if mid in seen:
                    continue
                seen.add(mid)
                hour = v[7] if len(v) > 7 else 12
                add(v[0], hour, v[1], proj, chat, "claude", is_long(v[1], v[2] + v[4] + v[5] + v[6]),
                    v[2], v[3], v[4], v[5], v[6])
        else:
            chat = f"chat:{chats[xid]}" if xid in chats else f"codex:{proj}"
            for k, v in (e.get("rows") or {}).items():
                parts = k.split("|")
                if len(parts) != 4:
                    continue
                day, hour, model, long = parts
                add(day, hour, model, proj, chat, "codex", long == "1", v[0], v[1], v[2], 0, 0)
    for day, hour, model, cwd, chat, long, tin, tout, cr, cw in hub or []:
        add(day, hour, model, _project(cwd), chat, "hub", long, tin, tout, cr, cw, 0)

    priced = {k: (cost_of(k[2], t, k[6]), price_for(k[2])[1], cost_parts(k[2], t, k[6])) for k, t in rows.items()}

    def summarize(start: str) -> dict:
        tot = _zero()
        by_model, by_proj, by_chat, by_src = {}, {}, {}, {}
        wd = [dict(_zero(), cost=0.0) for _ in range(7)]
        hr = [dict(_zero(), cost=0.0) for _ in range(24)]
        wsrc = [dict.fromkeys(SOURCES, 0) for _ in range(7)]   # tokens per source, for the stacked charts
        hsrc = [dict.fromkeys(SOURCES, 0) for _ in range(24)]
        cost = estimated = 0.0
        unpriced = 0
        parts = {"in": 0.0, "out": 0.0, "cr": 0.0, "cw": 0.0}
        for k, t in rows.items():
            day, hour, model, proj, chat, src, _long = k
            if day < start:
                continue
            c, est, cp = priced[k]
            for x in parts:
                parts[x] += (cp or {}).get(x, 0.0)
            if c is None:
                unpriced += _sum(t)
                c = 0.0
            elif est:
                estimated += c
            cost += c
            for x in TOK:
                tot[x] += t[x]
            wday = _dt.date.fromisoformat(day).weekday()
            n = _sum(t)
            wsrc[wday][src] += n
            hsrc[hour][src] += n
            for b in (by_model.setdefault(model, dict(_zero(), cost=0.0)), by_proj.setdefault(proj, dict(_zero(), cost=0.0)),
                      by_chat.setdefault(chat, dict(_zero(), cost=0.0)), by_src.setdefault(src, dict(_zero(), cost=0.0)),
                      wd[wday], hr[hour]):
                for x in TOK:
                    b[x] += t[x]
                b["cost"] += c

        def shaped(b: dict) -> dict:
            return {**{x: b[x] for x in TOK}, "tokens": _sum(b), "cost": round(b["cost"], 4)}

        def listed(d: dict, label, limit: int | None = None) -> list:
            items = sorted(d.items(), key=lambda kv: (-kv[1]["cost"], -_sum(kv[1])))
            out = [{"key": k, "name": label(k), **shaped(b)} for k, b in items]
            return out[:limit] if limit else out

        models = listed(by_model, friendly)
        for m in models:
            row, est = price_for(m["key"])
            m["id"] = m["key"]
            m["estimate"] = est
            m["priced"] = row is not None
            m["rate"] = rate_key(m["key"])[0]
        return {"tokens": _sum(tot), **tot, "cost": round(cost, 4), "estimated_cost": round(estimated, 4),
                "unpriced_tokens": unpriced, "cost_parts": {x: round(v, 4) for x, v in parts.items()},
                "by_source": {k: shaped(v) for k, v in by_src.items()},
                "by_model": models, "by_project": listed(by_proj, lambda k: k, 8),
                "by_chat": listed(by_chat, _chat_label, 8),
                "by_weekday": [{"day": n, **shaped(b), **ws} for n, b, ws in zip(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"), wd, wsrc)],
                "by_hour": [{"hour": h, **shaped(b), **hs} for h, (b, hs) in enumerate(zip(hr, hsrc))]}

    per_day = {d: {s: dict(_zero(), cost=0.0) for s in SOURCES} for d in days}
    for k, t in rows.items():
        b = per_day[k[0]][k[5]]
        for x in TOK:
            b[x] += t[x]
        b["cost"] += priced[k][0] or 0.0
    by_day = []
    for d, srcs in per_day.items():
        item = {"day": d, "tokens": 0, "cost": 0.0, "in": 0, "out": 0, "cache": 0}
        for s, b in srcs.items():
            n = _sum(b)
            item[s] = {"tokens": n, "cost": round(b["cost"], 4)}
            item["tokens"] += n
            item["cost"] += b["cost"]
            item["in"] += b["in"]
            item["out"] += b["out"]
            item["cache"] += b["cr"] + b["cw5"] + b["cw1"]
        item["cost"] = round(item["cost"], 4)
        by_day.append(item)
    return {"windows": {w: summarize(s) for w, s in win_start.items()}, "by_day": by_day,
            "prices": price_table(), "prices_checked": PRICES_CHECKED, "today": days[-1]}


def price_table() -> list[dict]:
    out = []
    for k, (pi, po, pcr, pc5, pc1) in RATES.items():
        lg = LONG.get(k)
        out.append({"id": k, "name": friendly(k), "provider": "anthropic" if k.startswith("claude-") else "openai",
                    "input": pi, "output": po, "cache_read": pcr, "cache_write_short": pc5, "cache_write_long": pc1,
                    "long_over": lg[0] if lg else None,
                    "long": dict(zip(("input", "output", "cache_read", "cache_write_short", "cache_write_long"), lg[1])) if lg else None})
    return out


# ---------------------------------------------------------------------------------------------------------

def _refresh() -> None:
    with _lock:
        files = scan()
        _mem["scanned_at"] = time.time()
        _save_cache(files)
        res = aggregate(files, chat_map(), hub=hub_rows())
        res["scanned_at"] = _mem["scanned_at"]
        _mem["result"], _mem["result_at"] = res, time.time()


def payload(wait: float = 6.0, force: bool = False) -> dict:
    """The newest aggregate. Rescans at most every FRESH_S seconds; a slow (cold) scan keeps running in the
    background and the previous result (or {"scanning": true}) is returned meanwhile."""
    fresh = _mem["result"] is not None and time.time() - _mem["result_at"] < FRESH_S
    if fresh and not force:
        return _mem["result"]
    t = _mem.get("thread")
    if t is None or not t.is_alive():
        t = threading.Thread(target=_refresh, daemon=True, name="usage-insights")
        _mem["thread"] = t
        t.start()
    t.join(wait)
    if _mem["result"] is not None:
        return {**_mem["result"], "scanning": t.is_alive()}
    return {"scanning": True}
