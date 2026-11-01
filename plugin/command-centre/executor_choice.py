"""Per-conversation executor choice: which worker model runs this conversation's requests.

The WebUI composer's model chip sets it through the sidecar; it lives in the command-centre session
state (state/sessions/<id>.json, key "executor_choice"). No choice = Anthropic, the most capable Opus.

  anthropic -> Claude Code (`claude -p --model <id> [--effort <level>]`, Claude Max OAuth)
  openai    -> Codex (`codex exec -m <id> [-c model_reasoning_effort=...]`, ChatGPT plan OAuth)

Only models verified to run on the user's subscriptions are listed (tiny `say hi` runs, 2026-10-28).
"""

from __future__ import annotations

from pathlib import Path

from .state import save_session_state, session_state

KEY = "executor_choice"
EFFORTS = ("low", "medium", "high")  # both CLIs accept these; None = the CLI's own default

CATALOG: dict[str, dict] = {
    "anthropic": {
        "name": "Anthropic",
        "worker": "Claude Code",
        "models": [
            {"id": "claude-opus-5-5", "name": "Opus 5.5", "desc": "Most capable"},
            {"id": "claude-fable-5-1", "name": "Fable 5.1", "desc": "Deepest reasoning, slower"},
            {"id": "claude-sonnet-5-5", "name": "Sonnet 5.5", "desc": "Fast and capable"},
            {"id": "claude-haiku-5-5", "name": "Haiku 5.5", "desc": "Quickest"},
        ],
    },
    "openai": {
        "name": "OpenAI",
        "worker": "Codex",
        "models": [
            {"id": "gpt-6-astra", "name": "GPT-6 Astra", "desc": "Frontier intelligence"},
            {"id": "gpt-6.1-sol", "name": "GPT-6.1 Sol", "desc": "Everyday workhorse"},
            {"id": "gpt-6.1-terra", "name": "GPT-6.1 Terra", "desc": "Balanced"},
            {"id": "gpt-6-luna", "name": "GPT-6 Luna", "desc": "Quickest"},
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


def providers_on() -> list[str]:
    """Providers whose CLI the user enabled (config/agents.yaml): Claude only, Codex only, or both."""
    on = _agents().enabled()
    return [p for p in CATALOG if AGENT_OF[p] in on]


def default_choice() -> dict:
    p = providers_on()[0]
    if p == DEFAULT["provider"]:
        return dict(DEFAULT)
    return {"provider": p, "model": CATALOG[p]["models"][0]["id"], "effort": None}


def model_info(provider: str, model: str) -> dict | None:
    for m in (CATALOG.get(provider) or {}).get("models", []):
        if m["id"] == model:
            return m
    return None


def valid(provider: str, model: str, effort: str | None) -> bool:
    return model_info(provider, model) is not None and (effort is None or effort in EFFORTS)


def get_choice(session_id: str) -> dict:
    """The conversation's choice, or the default. Always a complete, valid dict."""
    raw = session_state(session_id).get(KEY) or {}
    p, m, e = raw.get("provider"), raw.get("model"), raw.get("effort") or None
    if not (isinstance(p, str) and isinstance(m, str) and valid(p, m, e)) or p not in providers_on():
        d = default_choice()  # also when the picked agent was switched off in settings
        p, m, e = d["provider"], d["model"], d["effort"]
    info = model_info(p, m)
    return {"provider": p, "model": m, "effort": e, "name": info["name"], "desc": info["desc"],
            "worker": CATALOG[p]["worker"], "default": not raw}


def set_choice(session_id: str, provider: str, model: str, effort: str | None = None) -> dict:
    effort = (effort or "").strip().lower() or None
    if not valid(provider, model, effort) or provider not in providers_on():
        raise ValueError("unknown provider/model/effort")
    st = session_state(session_id)
    st[KEY] = {"provider": provider, "model": model, "effort": effort}
    save_session_state(session_id, st)
    return get_choice(session_id)


def public_catalog(available_openai: list[str] | None = None) -> dict:
    """Catalog for the picker. OpenAI models are trimmed to what this Codex login exposes."""
    out = {}
    for pid, p in CATALOG.items():
        if pid not in providers_on():
            continue
        models = p["models"]
        if pid == "openai" and available_openai:
            models = [m for m in models if m["id"] in available_openai]
        out[pid] = {"name": p["name"], "worker": p["worker"], "models": models}
    return {"providers": out, "efforts": list(EFFORTS), "default": default_choice(),
            "agents": _agents().enabled()}


def claude_args(model: str | None, effort: str | None) -> list[str]:
    out = ["--model", model] if model else []
    return out + (["--effort", effort] if effort else [])


def codex_effort_args(effort: str | None) -> list[str]:
    return ["-c", f'model_reasoning_effort="{effort}"'] if effort else []
