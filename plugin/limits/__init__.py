"""Usage limits — Hermes plugin.

/limit shows how much of each subscription the command centre has used: Claude Max (Claude Code),
ChatGPT via Codex and the Hermes hub (openai-codex OAuth), Higgsfield credits, ElevenLabs.
Everything is read-only and uses the existing subscription/OAuth logins; no API keys, no token
refreshes, and tokens are never logged or returned. Each source runs in its own try/except with a
short timeout, and results are cached for CACHE_TTL seconds.

Also runnable as a script (used by `hub limits`):  python __init__.py [--json] [--refresh]
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
CODEX_HOME = Path(os.environ.get("CODEX_HOME", HOME / ".codex"))
HERMES_HOME = Path(os.environ.get("HERMES_HOME", HOME / ".hermes"))
CACHE_TTL = 60
TIMEOUT = 8
CLI_TIMEOUT = 20  # the higgsfield/elevenlabs CLIs are slow to start when the Mac is busy
_cache: dict = {"at": 0.0, "rows": None}

CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"


# ---------------------------------------------------------------- helpers

def _now() -> float:
    return time.time()


def _to_epoch(v) -> float | None:
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return float(v) / 1000 if v > 1e11 else float(v)
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _window(minutes) -> str:
    if not minutes:
        return "?"
    m = int(minutes)
    if m % 1440 == 0:
        return f"{m // 1440}d"
    if m % 60 == 0:
        return f"{m // 60}h"
    return f"{m}m"


def _dur(seconds: float) -> str:
    s = max(0, int(seconds))
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m = s // 60
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


def _resets(epoch: float | None, now: float | None = None) -> str:
    if not epoch:
        return "—"
    now = now or _now()
    if epoch <= now:
        return "reset"
    local = datetime.fromtimestamp(epoch).strftime("%a %H:%M")
    return f"{_dur(epoch - now)} ({local})"


def _http_json(url: str, headers: dict) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "hermes-limits/1.0", **headers})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:  # never surface headers (they carry the bearer token)
        raise RuntimeError(f"HTTP {e.code}") from None


def _row(service, window, pct=None, used=None, limit=None, resets_at=None, note="", stale=None, source=""):
    return {"service": service, "window": window, "pct": pct, "used": used, "limit": limit,
            "resets_at": resets_at, "note": note, "stale_seconds": stale, "source": source}


def _jwt_claims(token: str) -> dict:
    try:
        p = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))
    except Exception:
        return {}


# ---------------------------------------------------------------- Claude Max

_CLAUDE_KIND = {"session": "5h session", "weekly_all": "7d all models"}
_CLAUDE_LEGACY = {"five_hour": "5h session", "seven_day": "7d all models", "seven_day_opus": "7d Opus",
                  "seven_day_sonnet": "7d Sonnet", "seven_day_oauth_apps": "7d OAuth apps"}


def parse_claude_usage(data: dict, label: str = "Claude Max") -> list[dict]:
    """Rows from Claude Code's /api/oauth/usage JSON (the endpoint statusline tools use)."""
    rows = []
    limits = data.get("limits")
    if isinstance(limits, list) and limits:
        for lim in limits:
            kind = lim.get("kind", "")
            window = _CLAUDE_KIND.get(kind)
            if not window:
                model = ((lim.get("scope") or {}).get("model") or {}).get("display_name")
                window = f"7d {model}" if model else kind.replace("_", " ")
            rows.append(_row(label, window, pct=lim.get("percent"), resets_at=_to_epoch(lim.get("resets_at")),
                             note="binding" if lim.get("is_active") else "", source="claude oauth/usage"))
    else:
        for key, window in _CLAUDE_LEGACY.items():
            v = data.get(key)
            if isinstance(v, dict) and v.get("utilization") is not None:
                rows.append(_row(label, window, pct=v.get("utilization"), resets_at=_to_epoch(v.get("resets_at")),
                                 source="claude oauth/usage"))
    extra = data.get("extra_usage") or {}
    if extra.get("is_enabled") and extra.get("monthly_limit"):
        rows.append(_row(label, "extra usage (month)", pct=extra.get("utilization"),
                         used=extra.get("used_credits"), limit=extra.get("monthly_limit"), source="claude oauth/usage"))
    return rows


def _claude_credentials() -> dict:
    """Claude Code's login: the macOS Keychain, else ~/.claude/.credentials.json (Windows, Linux)."""
    import sys
    if sys.platform == "darwin":
        out = subprocess.run(["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
                             capture_output=True, text=True, timeout=5)
        if out.returncode == 0:
            return json.loads(out.stdout).get("claudeAiOauth") or {}
    f = HOME / ".claude" / ".credentials.json"
    if not f.exists():
        raise RuntimeError("no Claude Code login found")
    return json.loads(f.read_text(encoding="utf-8")).get("claudeAiOauth") or {}


def claude_rows() -> list[dict]:
    label = "Claude Max"
    try:
        cred = _claude_credentials()
        tier = cred.get("rateLimitTier") or cred.get("subscriptionType") or ""
        m = re.search(r"max_(\d+x)", tier)
        label = f"Claude Max {m.group(1)}" if m else f"Claude {cred.get('subscriptionType') or ''}".strip()
        token = cred.get("accessToken")
        if not token:
            raise RuntimeError("no OAuth token")
        exp = _to_epoch(cred.get("expiresAt"))
        if exp and exp < _now():
            return [_row(label, "—", note="Claude Code token expired — run `claude` once to refresh")]
        data = _http_json(CLAUDE_USAGE_URL, {"Authorization": f"Bearer {token}",
                                             "anthropic-beta": "oauth-2025-04-20",
                                             "Content-Type": "application/json"})
        rows = parse_claude_usage(data, label)
        return rows or [_row(label, "—", note="usage endpoint returned no limits")]
    except Exception as e:
        return [_row(label, "—", note=f"unavailable ({_err(e)}) — fix: `claude auth login`")]


# ---------------------------------------------------------------- ChatGPT / Codex

def parse_codex_usage(data: dict) -> dict:
    """Normalise /backend-api/wham/usage (what `codex /status` reads) to {plan, windows:[...]}."""
    rl = data.get("rate_limit") or {}
    wins = []
    for key in ("primary_window", "secondary_window"):
        w = rl.get(key)
        if w:
            wins.append({"window": _window((w.get("limit_window_seconds") or 0) / 60),
                         "pct": w.get("used_percent"), "resets_at": _to_epoch(w.get("reset_at"))})
    models = [k for k, v in (data.get("model_usage") or {}).items() if not (v or {}).get("available", True)]
    return {"plan": data.get("plan_type") or "", "windows": wins, "limit_reached": rl.get("limit_reached"),
            "blocked_models": models, "account_id": data.get("account_id")}


def parse_codex_token_count(line: str) -> dict | None:
    """Parse one ~/.codex/sessions/**.jsonl line; returns {ts, plan, windows} for token_count events."""
    try:
        ev = json.loads(line)
    except (ValueError, TypeError):
        return None
    p = ev.get("payload") or {}
    if p.get("type") != "token_count" or not p.get("rate_limits"):
        return None
    rl = p["rate_limits"]
    wins = []
    for key in ("primary", "secondary"):
        w = rl.get(key)
        if w:
            wins.append({"window": _window(w.get("window_minutes")), "pct": w.get("used_percent"),
                         "resets_at": _to_epoch(w.get("resets_at"))})
    return {"ts": _to_epoch(ev.get("timestamp")), "plan": rl.get("plan_type") or "", "windows": wins}


def latest_codex_session_limits(root: Path | None = None, max_files: int = 8) -> dict | None:
    root = root or CODEX_HOME / "sessions"
    files = sorted(root.glob("*/*/*/*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:max_files]
    for f in files:
        try:
            lines = f.read_text(errors="ignore", encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            if '"token_count"' in line and '"rate_limits"' in line:
                hit = parse_codex_token_count(line)
                if hit and hit["windows"]:
                    return hit
    return None


def _codex_creds() -> list[tuple[str, str, str]]:
    """(who, access_token, account_id) for Codex CLI and Hermes' openai-codex OAuth."""
    creds = []
    try:
        t = json.loads((CODEX_HOME / "auth.json").read_text(encoding="utf-8")).get("tokens") or {}
        if t.get("access_token"):
            creds.append(("codex", t["access_token"], t.get("account_id") or ""))
    except Exception:
        pass
    try:
        pool = json.loads((HERMES_HOME / "auth.json").read_text(encoding="utf-8")).get("credential_pool", {}).get("openai-codex") or []
        for c in sorted(pool, key=lambda c: c.get("priority", 0)):
            if c.get("access_token"):
                acct = _jwt_claims(c["access_token"]).get("https://api.openai.com/auth", {}).get("chatgpt_account_id", "")
                creds.append(("hermes", c["access_token"], acct))
                break
    except Exception:
        pass
    return creds


def _hub_model() -> str:
    try:
        m = re.search(r'^model:\s*\n(?:\s+#.*\n)*\s+default:\s*"?([\w.\-]+)', (HERMES_HOME / "config.yaml").read_text(encoding="utf-8"), re.M)
        return m.group(1) if m else "hub"
    except OSError:
        return "hub"


def _codex_rows_from(label: str, info: dict, stale=None, source="") -> list[dict]:
    plan = f" {info['plan'].capitalize()}" if info.get("plan") else ""
    note = ""
    if info.get("limit_reached"):
        note = "limit reached"
    if info.get("blocked_models"):
        note = (note + "; " if note else "") + "blocked: " + ", ".join(info["blocked_models"])
    return [_row(f"ChatGPT{plan} · {label}", w["window"], pct=w["pct"], resets_at=w["resets_at"],
                 note=note, stale=stale, source=source) for w in info["windows"]]


CODEX_LABEL = "Codex gpt-6-astra"  # row label for Codex-as-worker; collect() filters on it


def codex_rows() -> list[dict]:
    hub = _hub_model()
    labels = {"codex": CODEX_LABEL, "hermes": f"Hub {hub}"}
    creds = _codex_creds()
    live: dict[str, dict] = {}
    errors = []
    for who, token, acct in creds:
        try:
            if acct and any(v.get("_acct") == acct for v in live.values()):
                for v in live.values():  # same ChatGPT account → one shared pool
                    if v.get("_acct") == acct:
                        v["_who"].append(who)
                continue
            h = {"Authorization": f"Bearer {token}", "originator": "codex_cli_rs"}
            if acct:
                h["ChatGPT-Account-Id"] = acct
            info = parse_codex_usage(_http_json(CODEX_USAGE_URL, h))
            info["_acct"], info["_who"] = acct or info.get("account_id"), [who]
            live[who] = info
        except Exception as e:
            errors.append(f"{who}: {_err(e)}")
    rows = []
    for info in live.values():
        label = " + ".join(labels[w] for w in info["_who"])
        if len(info["_who"]) > 1:
            label = "Codex + Hub (shared pool)"
        rows += _codex_rows_from(label, info, source="chatgpt wham/usage")
    covered = {w for i in live.values() for w in i["_who"]}
    if "codex" not in covered:  # fall back to the last Codex session's recorded numbers
        hit = latest_codex_session_limits()
        if hit:
            age = _now() - hit["ts"] if hit.get("ts") else None
            rows += _codex_rows_from(labels["codex"], hit, stale=age, source="codex session log")
        else:
            rows.append(_row(f"ChatGPT · {labels['codex']}", "—",
                             note="no data — fix: `codex login`" + (f" ({'; '.join(errors)})" if errors else "")))
    if "hermes" not in covered:
        rows.append(_row(f"ChatGPT · {labels['hermes']}", "—",
                         note="not connected — fix: `hermes auth add openai-codex --type oauth`"))
    return rows


# ---------------------------------------------------------------- Higgsfield

def parse_higgsfield(data: dict) -> list[dict]:
    plan = (data.get("subscription_plan_type") or "").capitalize()
    credits = data.get("credits")
    return [_row(f"Higgsfield {plan}".strip(), "credits", used=None, limit=None, pct=None,
                 note=f"{credits:,} credits left" if isinstance(credits, (int, float)) else "credits unknown",
                 source="higgsfield account status")]


def higgsfield_rows() -> list[dict]:
    try:
        out = subprocess.run(["higgsfield", "account", "status", "--json"], capture_output=True, text=True,
                             timeout=CLI_TIMEOUT)
        if out.returncode != 0:
            raise RuntimeError("not logged in")
        return parse_higgsfield(json.loads(out.stdout))
    except FileNotFoundError:
        return [_row("Higgsfield", "—", note="CLI not installed")]
    except subprocess.TimeoutExpired:
        return [_row("Higgsfield", "—", note=SLOW)]
    except Exception as e:
        return [_row("Higgsfield", "—", note=f"unavailable ({_err(e)}) — fix: `higgsfield auth login`")]


# ---------------------------------------------------------------- ElevenLabs

def parse_elevenlabs_subscription(data: dict) -> list[dict]:
    tier = (data.get("tier") or "").replace("_", " ").capitalize()
    used, limit = data.get("character_count"), data.get("character_limit")
    pct = round(100 * used / limit, 1) if isinstance(used, (int, float)) and limit else None
    return [_row(f"ElevenLabs {tier}".strip(), "credits (month)", pct=pct, used=used, limit=limit,
                 resets_at=_to_epoch(data.get("next_character_count_reset_unix")), source="elevenlabs subscription")]


def elevenlabs_rows() -> list[dict]:
    # 1) elevenlabs CLI: its own OAuth login in the Keychain (`elevenlabs auth login`), which has user_read
    slow = False
    try:
        out = subprocess.run(["elevenlabs", "user", "subscription", "get", "--format", "json"],
                             capture_output=True, text=True, timeout=CLI_TIMEOUT)
        if out.returncode == 0:
            data = json.loads(out.stdout)
            if "character_limit" in data:
                return parse_elevenlabs_subscription(data)
    except subprocess.TimeoutExpired:
        slow = True
    except Exception:
        pass
    # 2) the Hermes MCP OAuth token (works only if it was granted user_read)
    try:
        tok = json.loads((HERMES_HOME / "mcp-tokens" / "elevenlabs.json").read_text(encoding="utf-8"))
        if tok.get("access_token") and (tok.get("expires_at") or 0) > _now():
            return parse_elevenlabs_subscription(
                _http_json("https://api.us.elevenlabs.io/v1/user/subscription",
                           {"Authorization": f"Bearer {tok['access_token']}"}))
    except Exception:
        pass
    if slow:
        return [_row("ElevenLabs", "—", note=SLOW)]
    return [_row("ElevenLabs", "—", note="not connected — fix: `elevenlabs auth login`")]


# ---------------------------------------------------------------- collect + format

SLOW = "timed out (the Mac is busy) — `/limit refresh` to retry"
SOURCES = (claude_rows, codex_rows, higgsfield_rows, elevenlabs_rows)


def _err(e: Exception) -> str:
    msg = str(e) if isinstance(e, RuntimeError) else type(e).__name__
    return re.sub(r"(Bearer|sk-|eyJ)[\w.\-]+", "[redacted]", msg)[:80]


def _enabled_agents() -> list[str]:
    """config/agents.yaml (Claude Code, Codex or both); a missing hmem means an older install = both."""
    try:
        import sys
        root = str(Path(os.environ.get("COMMAND_CENTRE_HOME", HOME / "hermes")).expanduser())
        if root not in sys.path:
            sys.path.insert(0, root)
        from hmem import agents
        return agents.enabled()
    except Exception:
        return ["claude", "codex"]


# Rows for accounts this install isn't signed in to (or tools it doesn't have). /limit shows only
# the user's connected accounts; `bin/hub health` is where missing sign-ins are reported.
_UNCONNECTED = re.compile(r"^(not connected|CLI not installed|no data — fix|unavailable \(.*\) — fix)", re.I)


def connected(r: dict) -> bool:
    return not _UNCONNECTED.match(str(r.get("note") or ""))


def collect(refresh: bool = False) -> list[dict]:
    if not refresh and _cache["rows"] is not None and _now() - _cache["at"] < CACHE_TTL:
        return _cache["rows"]
    rows: list[dict] = []
    on = _enabled_agents()
    sources = [fn for fn in SOURCES if not (fn is claude_rows and "claude" not in on)]
    with ThreadPoolExecutor(max_workers=len(sources)) as ex:
        futs = [ex.submit(fn) for fn in sources]
        for fn, f in zip(sources, futs):
            try:
                rows += f.result(timeout=CLI_TIMEOUT + TIMEOUT)
            except Exception as e:
                rows.append(_row(fn.__name__.replace("_rows", "").capitalize(), "—", note=f"error ({_err(e)})"))
    if "codex" not in on:  # the hub's own ChatGPT login stays; only Codex-as-worker goes
        rows = [r for r in rows if not str(r.get("service", "")).split(" · ", 1)[-1].startswith(CODEX_LABEL)]
    rows = [r for r in rows if connected(r)]
    _cache.update(at=_now(), rows=rows)
    return rows


def _used(r: dict) -> str:
    if r["used"] is not None and r["limit"]:
        s = f"{r['used']:,} / {r['limit']:,}"
        return s + (f" ({r['pct']:g}%)" if r["pct"] is not None else "")
    if r["pct"] is not None:
        return f"{r['pct']:g}%"
    return r["note"] or "—"


def _cells(r: dict, now: float) -> list[str]:
    used = _used(r)
    extra = r["note"] if r["note"] and used != r["note"] else ""
    if r.get("stale_seconds") is not None:
        extra = (extra + "; " if extra else "") + f"stale {_dur(r['stale_seconds'])} (last Codex session)"
    return [r["service"], r["window"], used + (f" · {extra}" if extra else ""), _resets(r["resets_at"], now)]


HEAD = ["Service", "Window", "Used", "Resets in"]


def format_markdown(rows: list[dict], now: float | None = None) -> str:
    now = now or _now()
    lines = ["**Usage limits**", "", "| " + " | ".join(HEAD) + " |", "|" + "---|" * len(HEAD)]
    for r in rows:
        lines.append("| " + " | ".join(c.replace("|", "/") for c in _cells(r, now)) + " |")
    lines += ["", f"As of {datetime.fromtimestamp(now).strftime('%H:%M')} · cached {CACHE_TTL}s · `/limit refresh` to re-check"]
    return "\n".join(lines)


def format_plain(rows: list[dict], now: float | None = None) -> str:
    now = now or _now()
    data = [HEAD] + [_cells(r, now) for r in rows]
    widths = [max(len(row[i]) for row in data) for i in range(len(HEAD))]
    out = []
    for n, row in enumerate(data):
        out.append("  ".join(c.ljust(widths[i]) for i, c in enumerate(row)).rstrip())
        if n == 0:
            out.append("  ".join("─" * w for w in widths))
    return "\n".join(out)


# Plain-language window names for people (the raw `window` keys stay as-is in JSON/plain output).
_WINDOW_WORDS = {"5h session": "This session", "5h": "5-hour window", "7d": "This week",
                 "7d all models": "This week, all models", "credits (month)": "This month",
                 "extra usage (month)": "Extra usage this month", "credits": "Credits"}


def window_words(window: str) -> str:
    if window in _WINDOW_WORDS:
        return _WINDOW_WORDS[window]
    m = re.match(r"7d (.+)", window or "")
    return f"This week, {m.group(1)}" if m else window


def _clock(epoch: float, now: float) -> str:
    t, today = datetime.fromtimestamp(epoch), datetime.fromtimestamp(now)
    hm = t.strftime("%I:%M%p").lstrip("0").lower()
    if t.date() == today.date():
        return hm
    if (t.date() - today.date()).days == 1:
        return f"tomorrow {hm}"
    if (t.date() - today.date()).days < 7:
        return f"{t.strftime('%a')} {hm}"
    return f"{t:%a} {t.day} {t:%b}"  # %-d isn't supported on Windows


def _readable_line(r: dict, now: float) -> str:
    bits = []
    if r["used"] is not None and r["limit"]:
        unit = " credits" if "credits" in (r["window"] or "") else ""
        bits.append(f"{r['used']:,} of {r['limit']:,}{unit} used" + (f" ({r['pct']:g}%)" if r["pct"] is not None else ""))
    elif r["pct"] is not None:
        bits.append(f"{r['pct']:g}% used")
    if r["note"]:
        bits.append(r["note"])
    if r["resets_at"]:
        bits.append("resets now" if r["resets_at"] <= now
                    else f"resets in {_dur(r['resets_at'] - now)} ({_clock(r['resets_at'], now)})")
    if r.get("stale_seconds") is not None:
        bits.append(f"from the last Codex session, {_dur(r['stale_seconds'])} ago")
    text = " · ".join(bits) or "—"
    return text if r["window"] in ("—", "credits") else f"{window_words(r['window'])} — {text}"


def format_readable(rows: list[dict], now: float | None = None) -> str:
    """Grouped, table-free markdown: one heading per service, one plain sentence per window."""
    now = now or _now()
    out, last = ["**Usage limits**"], None
    for r in rows:
        if r["service"] != last:
            out += ["", f"**{r['service']}**"]
            last = r["service"]
        out.append(f"- {_readable_line(r, now)}")
    out += ["", f"_Updated {_clock(now, now)} · `/limit refresh` to re-check_"]
    return "\n".join(out)


LIMITS_FENCE = "tabbie-limits"  # the WebUI's limits-view.js turns this block into cards


def format_webui(rows: list[dict], now: float | None = None) -> str:
    """Machine-readable block for the WebUI card view, followed by the readable fallback."""
    now = now or _now()
    payload = {"v": 1, "as_of": now, "cache_ttl": CACHE_TTL, "rows": _public(rows, now)}
    return f"```{LIMITS_FENCE}\n{json.dumps(payload, separators=(',', ':'))}\n```\n\n" + format_readable(rows, now)


def _public(rows: list[dict], now: float | None = None) -> list[dict]:
    now = now or _now()
    return [{**r, "resets_in": _resets(r["resets_at"], now),
             "resets_at": datetime.fromtimestamp(r["resets_at"], timezone.utc).isoformat() if r["resets_at"] else None}
            for r in rows]


# ---------------------------------------------------------------- Hermes wiring

_MESSAGING = {"telegram", "discord", "slack", "whatsapp", "signal", "matrix", "sms", "email", "mattermost",
              "imessage", "bluebubbles", "homeassistant", "dingtalk", "feishu", "wecom", "weixin", "qq"}


def _cmd_limit(raw_args: str = "", platform: str | None = None, **kwargs):
    refresh = "refresh" in (raw_args or "").lower()
    rows = collect(refresh=refresh)
    if not rows:
        return "No connected accounts to show. Sign in with `bin/setup` on the Mac; `bin/hub health` lists what's missing."
    if not platform or platform in ("webui", "web"):
        return format_webui(rows)  # WebUI (no platform passed): cards via limits-view.js
    if platform == "api_server":
        return format_readable(rows)
    if str(platform).lower() in _MESSAGING:
        return "```\n" + format_plain(rows) + "\n```"
    return format_plain(rows)  # cli / tui / terminal surfaces


def _tool_usage_limits(params=None, **kwargs):
    refresh = bool((params or {}).get("refresh"))
    return json.dumps({"limits": _public(collect(refresh=refresh)),
                       "note": "pct = % of the window used; resets_in is relative to now"})


def register(ctx):
    ctx.register_command("limit", _cmd_limit,
                         description="Usage limits: Claude Max, ChatGPT (Codex + hub), Higgsfield, ElevenLabs",
                         args_hint="[refresh]")
    ctx.register_tool(name="usage_limits", toolset="limits", emoji="", handler=_tool_usage_limits,
                      schema={"name": "usage_limits",
                              "description": "How much subscription usage is left: Claude Max 5h/7d %, ChatGPT/Codex "
                                             "(Codex + Hermes hub) 5h/7d %, Higgsfield credits, ElevenLabs credits, "
                                             "with reset times. Use for 'how much usage do I have left'.",
                              "parameters": {"type": "object",
                                             "properties": {"refresh": {"type": "boolean",
                                                                        "description": "bypass the 60s cache"}},
                                             "required": []}})


if __name__ == "__main__":
    import sys
    rows = collect(refresh=True)
    if "--json" in sys.argv:
        print(json.dumps(_public(rows), indent=2))
    else:
        print(format_plain(rows))
