"""Per-conversation executor choice: which worker model runs this conversation's requests.

The WebUI composer's model chip sets it through the sidecar; it lives in the command-centre session
state (state/sessions/<id>.json, key "executor_choice"). No choice = Anthropic, the most capable Opus.

  anthropic -> Claude Code (`claude -p --model <id> [--effort <level>]`, Claude Max OAuth)
  openai    -> Codex (`codex exec -m <id> [-c model_reasoning_effort=...]`, ChatGPT plan OAuth)
  <other>   -> a linked model provider (model_providers: DeepSeek, Kimi, an API key, Ollama...), run in
               Claude Code or Codex pointed at that provider. Listed only once it is linked.

Only models verified to run on the user's subscriptions are listed (tiny `say hi` runs, 2026-10-28).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from . import model_latest
from .common import STATE_DIR, write_json
from .state import save_session_state, session_state

KEY = "executor_choice"
EFFORTS = ("low", "medium", "high")  # both CLIs accept these; None = the CLI's own default

CATALOG: dict[str, dict] = {
    "anthropic": {
        "name": "Anthropic",
        "worker": "Claude Code",
        "models": [
            {"id": "claude-opus-5-5", "name": "Opus 5.5", "desc": "Most capable", "tier": "top"},
            {"id": "claude-fable-5-2", "name": "Fable 5.2", "desc": "Deepest reasoning, slower", "tier": "top"},
            {"id": "claude-sonnet-5-5", "name": "Sonnet 5.5", "desc": "Fast and capable", "tier": "mid"},
            {"id": "claude-haiku-5-5", "name": "Haiku 5.5", "desc": "Quickest", "tier": "fast"},
        ],
    },
    "openai": {
        "name": "OpenAI",
        "worker": "Codex",
        "models": [
            {"id": "gpt-6-astra", "name": "GPT-6 Astra", "desc": "Frontier intelligence", "tier": "top"},
            {"id": "gpt-6.1-sol", "name": "GPT-6.1 Sol", "desc": "Everyday workhorse", "tier": "top"},
            {"id": "gpt-6.1-terra", "name": "GPT-6.1 Terra", "desc": "Balanced", "tier": "mid"},
            {"id": "gpt-6-luna", "name": "GPT-6 Luna", "desc": "Quickest", "tier": "fast"},
        ],
    },
}

DEFAULT = {"provider": "anthropic", "model": "claude-opus-5-5", "effort": None}
AGENT_OF = {"anthropic": "claude", "openai": "codex"}


def _agents():
    import sys
    home = str(Path(__file__).resolve().parents[2])
    if home not in sys.path:
        sys.path.insert(0, home)
    from hmem import agents
    return agents


def _mp():
    from . import model_providers
    return model_providers


WORKER_NAME = {"claude": "Claude Code", "codex": "Codex"}
TIER_DESC = {"top": "Most capable", "mid": "Balanced", "fast": "Quickest"}


def worker_of(provider: str) -> str | None:
    """The CLI that runs this provider's models: claude or codex."""
    if provider in AGENT_OF:
        return AGENT_OF[provider]
    p = _mp().get(provider)
    return p.get("worker", "claude") if p else None


def is_endpoint(provider: str | None) -> bool:
    """A linked model provider (not the Claude / ChatGPT subscription logins)."""
    return bool(provider) and provider not in CATALOG


def endpoints_on() -> list[str]:
    """Linked model providers whose CLI is installed."""
    import shutil
    try:
        ids = _mp().linked_ids()
    except Exception:
        return []
    return [pid for pid in ids if shutil.which(worker_of(pid) or "")]


def providers_on() -> list[str]:
    """Subscriptions the user enabled (config/agents.yaml), then linked model providers."""
    on = _agents().enabled()
    return [p for p in CATALOG if AGENT_OF[p] in on] + endpoints_on()


# The model new chats start on: the last one picked in the composer, or the one set in Settings →
# Preferences (POST /executor {default: true}). Absent / no longer runnable = the built-in default below.
def _new_chat_file() -> Path:
    return Path(os.environ.get("TABBIE_NEW_CHAT_FILE") or STATE_DIR / "new-chat-choice.json")


def saved_default() -> dict | None:
    try:
        raw = json.loads(_new_chat_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    p, m, e = raw.get("provider"), raw.get("model"), raw.get("effort") or None
    if not (isinstance(p, str) and isinstance(m, str)):
        return None
    m = model_latest.upgrade(m) or m
    if not valid(p, m, e) or p not in providers_on():
        return None
    return {"provider": p, "model": m, "effort": e}


def set_default(provider: str, model: str, effort: str | None = None) -> dict:
    effort = (effort or "").strip().lower() or None
    model = model_latest.upgrade(model) or model
    if not valid(provider, model, effort) or provider not in providers_on():
        raise ValueError("unknown provider/model/effort")
    write_json(_new_chat_file(), {"provider": provider, "model": model, "effort": effort})
    return new_chat_choice()


def default_choice() -> dict:
    saved = saved_default()
    if saved:
        return saved
    on = providers_on()
    p = on[0] if on else DEFAULT["provider"]
    if p == DEFAULT["provider"]:
        return {**DEFAULT, "model": model_latest.upgrade(DEFAULT["model"])}
    ms = _models(p)
    return {"provider": p, "model": ms[0]["id"] if ms else DEFAULT["model"], "effort": None}


def _models(provider: str) -> list[dict]:
    if provider in CATALOG:
        # each family's newest model (model_latest): Fable 5.1 becomes Fable 5.2 the day 5.2 ships
        model_latest.refresh_soon()
        out = []
        for m in CATALOG[provider]["models"]:
            nid = model_latest.upgrade(m["id"])
            out.append(m if nid == m["id"] else {**m, "id": nid, "name": model_latest.display_name(nid)})
        return out
    mp = _mp()
    label = mp.BILLING_LABEL.get((mp.get(provider) or {}).get("billing"), "")
    return [{**m, "desc": m.get("desc") or " · ".join(x for x in (TIER_DESC.get(m.get("tier"), ""), label) if x)}
            for m in mp.models(provider)]


def provider_name(provider: str) -> str:
    if provider in CATALOG:
        return CATALOG[provider]["name"]
    return (_mp().get(provider) or {}).get("name") or provider


def model_info(provider: str, model: str) -> dict | None:
    model = model_latest.upgrade(model) or model
    for m in _models(provider) if (provider in CATALOG or _mp().get(provider)) else []:
        if m["id"] == model:
            return m
    if is_endpoint(provider) and _mp().get(provider):
        m = _mp().model_info(provider, model)  # typed by the user, not in the fetched list
        return {**m, "desc": TIER_DESC.get(m.get("tier"), "")} if m else None
    return None


def valid(provider: str, model: str, effort: str | None) -> bool:
    return model_info(provider, model) is not None and (effort is None or effort in EFFORTS)


def takes_effort(provider: str) -> bool:
    """Claude / OpenAI models take an effort level; other providers' endpoints may reject it."""
    return not is_endpoint(provider) or bool((_mp().get(provider) or {}).get("official"))


def label(provider: str, name: str) -> str:
    """"DeepSeek V4 Pro", "Kimi K2" ...: the provider name only when the model name lacks it."""
    pn = provider_name(provider)
    return name if name.lower().startswith(pn.lower().split()[0]) else f"{pn} {name}"


def _worker_label(provider: str) -> str:
    w = WORKER_NAME.get(worker_of(provider) or "", "")
    return w if not is_endpoint(provider) else f"{w} → {provider_name(provider)}"


def get_choice(session_id: str) -> dict:
    """The conversation's choice, or the default. Always a complete, valid dict."""
    raw = session_state(session_id).get(KEY) or {}
    p, m, e = raw.get("provider"), raw.get("model"), raw.get("effort") or None
    if isinstance(m, str):
        m = model_latest.upgrade(m)  # a chat set to an older model runs its family's newest
    ok = isinstance(p, str) and isinstance(m, str) and valid(p, m, e)
    on = providers_on()
    if ok and p not in on and len(on) > 0 and not is_endpoint(p):
        p, m = (peer := peer_model(p, m))["provider"], peer["model"]  # agent switched off: its equivalent
    elif not ok or p not in on:
        d = default_choice()
        p, m, e = d["provider"], d["model"], d["effort"]
    info = model_info(p, m) or {"name": m, "desc": ""}
    return {"provider": p, "model": m, "effort": e, "name": info["name"], "desc": info.get("desc", ""),
            "worker": _worker_label(p), "agent": worker_of(p), "endpoint": is_endpoint(p), "default": not raw}


def new_chat_choice() -> dict:
    """What a chat that does not exist yet will run: the default, in get_choice's shape."""
    d = default_choice()
    info = model_info(d["provider"], d["model"]) or {"name": d["model"], "desc": ""}
    return {**d, "name": info["name"], "desc": info.get("desc", ""), "worker": _worker_label(d["provider"]),
            "agent": worker_of(d["provider"]), "endpoint": is_endpoint(d["provider"]), "default": True,
            "default_saved": saved_default() is not None}


def set_choice(session_id: str, provider: str, model: str, effort: str | None = None) -> dict:
    effort = (effort or "").strip().lower() or None
    model = model_latest.upgrade(model) or model
    if not valid(provider, model, effort) or provider not in providers_on():
        raise ValueError("unknown provider/model/effort")
    st = session_state(session_id)
    st[KEY] = {"provider": provider, "model": model, "effort": effort}
    save_session_state(session_id, st)
    try:  # the composer's last pick is what the next new chat starts on (Settings → Preferences shows it)
        write_json(_new_chat_file(), st[KEY])
    except OSError:
        pass
    return get_choice(session_id)


def public_catalog(available_openai: list[str] | None = None) -> dict:
    """Catalog for the picker: only what this user can run. OpenAI models are trimmed to what this
    Codex login exposes; linked providers carry their billing label (Plan / Pay per use / On this computer)."""
    out = {}
    for pid in providers_on():
        models = _models(pid)
        if pid == "openai" and available_openai:
            models = [m for m in models if m["id"] in available_openai]
        entry = {"name": provider_name(pid), "worker": _worker_label(pid), "agent": worker_of(pid), "models": models}
        entry["effort"] = takes_effort(pid)
        if is_endpoint(pid):
            p = _mp().get(pid) or {}
            entry.update(endpoint=True, billing=p.get("billing"), billing_label=_mp().BILLING_LABEL.get(p.get("billing"), ""))
        out[pid] = entry
    return {"providers": out, "efforts": list(EFFORTS), "default": default_choice(),
            "agents": _agents().enabled()}


# Auto effort (the picker's "Auto"): Tabbie picks per request instead of leaving it to each CLI's default
# (Claude Code would inherit the user's own effortLevel, often high, on every run). Medium for nearly
# everything; low for short questions and one-step chores; high only when the user asks for it.
_HIGH_RX = re.compile(r"\b(ultrathink|think (really |very )?(hard|deeply|carefully)|deep(ly)? (think|reason)|"
                      r"(max|maximum|high|highest) effort|reason (it )?(through )?carefully|as thoroughly as (you|possible)|"
                      r"exhaustive(ly)?)\b", re.I)
_LOW_RX = re.compile(r"^(what|which|where|who|when|why|how (many|much|long)|is|are|does|do|did|can|could|show|list|"
                     r"open|run|start|stop|restart|rename|bump|commit|push|pull|undo|revert|fix (the |a |this )?typo)\b",
                     re.I)


def auto_effort(text: str | None) -> str:
    t = " ".join(str(text or "").split())
    if _HIGH_RX.search(t):
        return "high"
    if len(t.split()) <= 15 and _LOW_RX.search(t):
        return "low"
    return "medium"


def claude_args(model: str | None, effort: str | None) -> list[str]:
    model = model_latest.upgrade(model)
    out = ["--model", model] if model else []
    return out + (["--effort", effort] if effort else [])


def codex_effort_args(effort: str | None) -> list[str]:
    return ["-c", f'model_reasoning_effort="{effort}"'] if effort else []


# Same-tier model on the other provider, for hand-offs and limit switches. Override per model in
# router.yaml: dynamic_agents.models: {claude-opus-5-5: gpt-6-astra, ...}.
PEER = {
    "claude-opus-5-5": "gpt-6.1-sol", "claude-fable-5-2": "gpt-6-astra",
    "claude-sonnet-5-5": "gpt-6.1-terra", "claude-haiku-5-5": "gpt-6-luna",
    "gpt-6.1-sol": "claude-opus-5-5", "gpt-6-astra": "claude-fable-5-2",
    "gpt-6.1-terra": "claude-sonnet-5-5", "gpt-6-luna": "claude-haiku-5-5",
}


# by family, so the pairing survives new versions on either side
PEER_FAMILY = {"opus": "sol", "fable": "astra", "sonnet": "terra", "haiku": "luna"}
PEER_FAMILY.update({v: k for k, v in list(PEER_FAMILY.items())})


def _peer_by_family(model: str | None) -> str | None:
    p = model_latest.parse(model or "")
    if not p or p[1] not in PEER_FAMILY:
        return None
    other = "openai" if p[0] == "anthropic" else "anthropic"
    fam = PEER_FAMILY[p[1]]
    for m in _models(other):
        q = model_latest.parse(m["id"])
        if q and q[1] == fam:
            return m["id"]
    return None


def tier_of(provider: str, model: str | None) -> str:
    m = model_info(provider, model or "") if model else None
    return (m or {}).get("tier") or "top"


def peer_model(provider: str, model: str | None, overrides: dict | None = None,
               available_openai: list[str] | None = None, exclude: list[str] | tuple = ()) -> dict:
    """{provider, model, name} of a same-tier model elsewhere, for hand-offs and limit switches.
    Claude <-> Codex first (the PEER map, router.yaml overrides); when that side is off or excluded, a
    linked model provider at the same tier. An OpenAI peer the Codex login does not expose falls back
    to the first catalog model it does."""
    on = providers_on()
    other = "openai" if provider == "anthropic" else "anthropic"
    if provider not in CATALOG or other not in on or other in exclude:
        tier = tier_of(provider, model)
        for pid in [*[p for p in CATALOG if p in on], *endpoints_on()]:
            if pid == provider or pid in exclude or pid not in on:
                continue
            ms = _models(pid)
            if pid == "openai" and available_openai:
                ms = [m for m in ms if m["id"] in available_openai] or ms
            if ms:
                pick = next((m for m in ms if m.get("tier") == tier), ms[0])
                return {"provider": pid, "model": pick["id"], "name": pick.get("name") or pick["id"]}
        if provider not in CATALOG:  # nothing else is on: the subscriptions' defaults
            other = "anthropic"
    want = (overrides or {}).get(model or "") or PEER.get(model or "") or _peer_by_family(model)
    want = model_latest.upgrade(want)
    ids = [m["id"] for m in _models(other)]
    if other == "openai" and available_openai:
        ids = [i for i in ids if i in available_openai] or ids
    if want not in ids:
        want = ids[0]
    return {"provider": other, "model": want, "name": model_info(other, want)["name"] if model_info(other, want) else want}
