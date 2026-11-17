"""Secrets: API keys Tabbie's workers can use, kept in 1Password, never shown.

Where they live: a 1Password vault (default "Tabbie") that a 1Password service account can read and write.
The service account token sits in the macOS Keychain (service ai.tabbie.1password); nothing here writes it to
disk, logs it or returns it. Workers never get it either: bin/with-secrets runs their command through op_sdk.py
`run`, which injects the secrets as environment variables of that one process and masks them in its output.

1Password is reached with its Python SDK (op_sdk.py), not the `op` CLI: `op` reads the 1Password app's data,
which macOS guards with a permission prompt per background process ("would like to access data from other
apps") and `op` hangs until it's clicked. The SDK never touches the app, so it works unattended.

What the browser sees (Settings → Accounts → Secrets): each secret's name, its environment variable name and
its provider. Never a value. The one exception is Rotate: the provider makes a new key, it's saved to
1Password, and the new value is returned once in that response so the user can copy it. It is not kept
anywhere else, so it can't be shown again.

Each item is an API Credential with fields: credential (concealed, the value), env (variable name),
provider (resend, cloudflare, supabase, ...), optional key_id (the provider's id for this key, for revoking it).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from .common import STATE_DIR, read_json, write_json

KEYCHAIN_SERVICE = "ai.tabbie.1password"
KEYCHAIN_ACCOUNT = "service-account"
VAULT = os.environ.get("TABBIE_SECRETS_VAULT", "Tabbie")
INDEX = STATE_DIR / "secrets-index.json"      # names only: {items: {id: {title, env, provider, updated}}}
ENV_RX = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
TITLE_MAX, VALUE_MAX = 80, 8000
OP_TIMEOUT = 60

# providers Tabbie knows: what to call them, the usual variable name, and whether Tabbie can rotate the key
PROVIDERS = {
    "resend": {"label": "Resend", "env": "RESEND_API_KEY", "rotate": True,
               "how": "Makes a new full-access key with the current one, then deletes the old key."},
    "cloudflare": {"label": "Cloudflare API token", "env": "CLOUDFLARE_API_TOKEN", "rotate": True,
                   "how": "Rolls the token (same permissions, new value). The token needs the "
                          "“API Tokens: Edit” permission to roll itself."},
    "turnstile": {"label": "Cloudflare Turnstile", "env": "TURNSTILE_SECRET_KEY", "rotate": False},
    "supabase": {"label": "Supabase", "env": "SUPABASE_ACCESS_TOKEN", "rotate": False},
    "openai": {"label": "OpenAI", "env": "OPENAI_API_KEY", "rotate": False},
    "github": {"label": "GitHub", "env": "GITHUB_TOKEN", "rotate": False},
    "stripe": {"label": "Stripe", "env": "STRIPE_SECRET_KEY", "rotate": False},
    "other": {"label": "Other", "env": "", "rotate": False},
}


class SecretError(Exception):
    """A message safe to show the user (never contains a secret value)."""


# ── the service account token (Keychain) ───────────────────────────────────────────────────────────
def _token() -> str | None:
    if not shutil.which("security"):
        return None
    r = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w"],
                       capture_output=True, text=True)
    t = r.stdout.strip() if r.returncode == 0 else ""
    return t if t.startswith("ops_") and len(t) > 200 else None


def save_token(token: str) -> None:
    """Store the service account token in the Keychain (stdin to `security -i`, so never in a process list)."""
    token = (token or "").strip()
    if not token.startswith("ops_") or len(token) < 200 or any(c.isspace() or c in "\"'\\" for c in token):
        raise SecretError("That doesn't look like a 1Password service account token (it starts with ops_ and is long).")
    cmd = (f'add-generic-password -U -a {KEYCHAIN_ACCOUNT} -s {KEYCHAIN_SERVICE} '
           f'-l "Tabbie 1Password service account" -w {token}\n')
    r = subprocess.run(["security", "-i"], input=cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SecretError("Couldn't save it in the Keychain.")
    INDEX.unlink(missing_ok=True)


def op_env(token: str | None = None) -> dict:
    tok = token or _token()
    if not tok:
        raise SecretError("1Password isn't connected yet.")
    env = {k: v for k, v in os.environ.items() if not k.startswith("OP_")}
    env["OP_SERVICE_ACCOUNT_TOKEN"] = tok
    return env


SDK_PIN = "onepassword-sdk==0.4.2"
HELPER = Path(__file__).resolve().with_name("op_sdk.py")


def _uv() -> str | None:
    home = Path.home()
    for c in (shutil.which("uv"), home / ".local/bin/uv", "/opt/homebrew/bin/uv", "/usr/local/bin/uv", home / ".cargo/bin/uv"):
        if c and Path(c).is_file():
            return str(c)
    return None


def sdk_cmd(args: list[str]) -> list[str]:
    """The op_sdk.py helper with the SDK (uv caches it after the first run)."""
    uv = _uv()
    if not uv:
        raise SecretError("uv isn't installed (Tabbie needs it to talk to 1Password).")
    return [uv, "run", "-q", "--no-project", "--with", SDK_PIN, "python", "-I", str(HELPER), *args]


def available() -> bool:
    return bool(_uv())


def _op(args: list[str], stdin: str | None = None, token: str | None = None) -> str:
    try:
        r = subprocess.run(sdk_cmd(args), input=stdin, capture_output=True, text=True, timeout=OP_TIMEOUT,
                           env=op_env(token))
    except FileNotFoundError:
        raise SecretError("uv isn't installed (Tabbie needs it to talk to 1Password).") from None
    except subprocess.TimeoutExpired:
        raise SecretError("1Password didn't answer in time.") from None
    if r.returncode != 0:
        msg = re.sub(r"^\[ERROR\]\s*\S+\s+\S+\s*", "", (r.stderr or "").strip().splitlines()[-1] if r.stderr.strip() else "")
        raise SecretError(f"1Password: {msg[:160] or 'failed'}")
    return r.stdout


# ── status + list (names only) ─────────────────────────────────────────────────────────────────────
def status() -> dict:
    out = {"op": available(), "connected": False, "vault": VAULT, "error": ""}
    if not out["op"]:
        return out
    if not _token():
        return out
    try:
        _op(["vault", "get", VAULT, "--format", "json"])
        out["connected"] = True
    except SecretError as exc:
        out["error"] = str(exc)
    return out


def _meta(item_id: str) -> dict:
    """An item's non-secret fields (env, provider, key_id). Concealed fields are dropped right here, unread."""
    data = json.loads(_op(["item", "get", item_id, "--vault", VAULT, "--format", "json"]) or "{}")
    keep = {"env", "provider", "key_id"}
    fields = [f for f in data.get("fields") or [] if isinstance(f, dict)]
    out = {str(f.get("label")): str(f.get("value") or "") for f in fields
           if f.get("type") != "CONCEALED" and f.get("label") in keep}
    # which field holds the value (credential / password / anything concealed): its id only, never its value
    hidden = [f for f in fields if f.get("type") == "CONCEALED" and f.get("id")]
    pick = next((f for f in hidden if f.get("id") in ("credential", "password")), hidden[0] if hidden else None)
    out["_field"] = str(pick["id"]) if pick else ""
    return out


def env_from_title(title: str) -> str:
    """'OpenAI API key' -> OPENAI_API_KEY (for keys added straight in the 1Password app)."""
    e = re.sub(r"[^A-Z0-9]+", "_", str(title or "").upper()).strip("_")
    e = re.sub(r"^[0-9_]+", "", e)[:64]
    return e if ENV_RX.match(e) else ""


def guess_provider(title: str) -> str:
    t = str(title or "").lower()
    for k in PROVIDERS:
        if k != "other" and k in t:
            return "turnstile" if "turnstile" in t else k
    return "other"


def _tags(env: str, provider: str) -> list[str]:
    """Variable + provider ride on the item's tags, so ONE `op item list` gives every row (no lookup per item)."""
    return ["tabbie", f"env:{env}", f"provider:{provider}"]


def _from_tags(tags) -> dict:
    out = {}
    for t in tags or []:
        k, _, v = str(t).partition(":")
        if k in ("env", "provider") and v:
            out[k] = v
    return out


def items(force: bool = False) -> list[dict]:
    raw = json.loads(_op(["item", "list", "--vault", VAULT, "--format", "json"]) or "[]")
    idx = {} if force else (read_json(INDEX, {}) or {}).get("items", {})
    out, fresh = [], {}
    for it in raw:
        iid, upd = it.get("id"), it.get("updated_at")
        m = _from_tags(it.get("tags"))
        field = "credential"
        if "env" not in m:  # added in the 1Password app without Tabbie's tags: read its fields once (cached)
            row = idx.get(iid)
            if not row or row.get("updated") != upd or "field" not in row:
                f = _meta(iid)
                row = {"env": f.get("env") or env_from_title(it.get("title")),
                       "provider": f.get("provider") or guess_provider(it.get("title")),
                       "field": f.get("_field", ""), "updated": upd}
            m = {"env": row["env"], "provider": row["provider"]}
            field = row.get("field", "")
        fresh[iid] = {"env": m.get("env", ""), "provider": m.get("provider", "other"), "field": field, "updated": upd}
        prov = m.get("provider", "other") if m.get("provider") in PROVIDERS else "other"
        p = PROVIDERS[prov]
        out.append({"id": iid, "title": it.get("title"), "env": m.get("env", ""), "provider": prov, "field": field,
                    "provider_label": p["label"], "rotatable": p["rotate"], "rotate_how": p.get("how", ""),
                    "updated": upd})
    write_json(INDEX, {"items": fresh, "at": time.time()})
    out.sort(key=lambda r: (r["title"] or "").lower())
    return out


def env_refs(strict: bool = False) -> dict[str, str]:
    """{ENV_NAME: op://vault/item/credential} for op_sdk.py `run` (no values). strict: raise instead of {} when
    1Password can't be reached, so `with-secrets --list` says why rather than "no secrets"."""
    try:
        rows = items()
    except SecretError:
        if strict:
            raise
        return {}
    out = {}
    for r in rows:  # first one wins if two items end up with the same name
        if ENV_RX.match(r["env"] or "") and r.get("field") and r["env"] not in out:
            out[r["env"]] = f"op://{VAULT}/{r['id']}/{r['field']}"
    return out


# ── add / replace / delete ─────────────────────────────────────────────────────────────────────────
def _template(title: str, value: str, env: str, provider: str, key_id: str = "") -> str:
    fields = [{"id": "credential", "type": "CONCEALED", "label": "credential", "value": value},
              {"id": "env", "type": "STRING", "label": "env", "value": env},
              {"id": "provider", "type": "STRING", "label": "provider", "value": provider}]
    if key_id:
        fields.append({"id": "key_id", "type": "STRING", "label": "key_id", "value": key_id})
    return json.dumps({"title": title, "category": "API_CREDENTIAL", "fields": fields, "tags": _tags(env, provider)})


def _check(title: str, env: str, provider: str, value: str | None, skip_id: str | None = None) -> None:
    if not title or len(title) > TITLE_MAX:
        raise SecretError("Give it a name (up to 80 characters).")
    if not ENV_RX.match(env):
        raise SecretError("The variable name must be capitals, digits and _ (like RESEND_API_KEY).")
    if provider not in PROVIDERS:
        raise SecretError("Unknown provider.")
    if value is not None and (not value or len(value) > VALUE_MAX):
        raise SecretError("Paste the secret value.")
    if any(r["env"] == env and r["id"] != skip_id for r in items()):
        raise SecretError(f"{env} is already used by another secret.")


def add(body: dict) -> dict:
    title, env = str(body.get("title") or "").strip(), str(body.get("env") or "").strip().upper()
    provider, value = str(body.get("provider") or "other"), str(body.get("value") or "").strip()
    _check(title, env, provider, value)
    _op(["item", "create", "--vault", VAULT, "--format", "json", "-"], stdin=_template(title, value, env, provider))
    return {"ok": True, "items": items()}


def update(body: dict) -> dict:
    """Rename / change variable or provider, and optionally replace the value (blank value = keep it)."""
    iid = str(body.get("id") or "")
    cur = next((r for r in items() if r["id"] == iid), None)
    if not cur:
        raise SecretError("That secret is gone.")
    title = str(body.get("title") or cur["title"]).strip()
    env = str(body.get("env") or cur["env"]).strip().upper()
    provider = str(body.get("provider") or cur["provider"])
    value = str(body.get("value") or "").strip()
    _check(title, env, provider, value or None, skip_id=iid)
    args = ["item", "edit", iid, "--vault", VAULT, "--tags", ",".join(_tags(env, provider)),
            f"title={title}", f"env[text]={env}", f"provider[text]={provider}"]
    _op(args)  # names only on the command line
    if value:
        _set_value(iid, value)
    return {"ok": True, "items": items()}


def _set_value(iid: str, value: str, key_id: str = "") -> None:
    """Replace the concealed value: piped JSON (stdin), so the value never appears in a process list.
    A piped edit replaces ALL fields, so the item's other (non-secret) fields are sent back with it."""
    meta = _meta(iid)
    if key_id:
        meta["key_id"] = key_id
    patch = {"fields": [{"id": "credential", "type": "CONCEALED", "label": "credential", "value": value}]
             + [{"id": k, "type": "STRING", "label": k, "value": v} for k, v in meta.items() if v]}
    if meta.get("env"):
        patch["tags"] = _tags(meta["env"], meta.get("provider", "other"))
    _op(["item", "edit", iid, "--vault", VAULT, "--format", "json", "-"], stdin=json.dumps(patch))


def delete(body: dict) -> dict:
    iid = str(body.get("id") or "")
    if not re.fullmatch(r"[a-z0-9]{26}", iid):
        raise SecretError("Bad id.")
    _op(["item", "delete", iid, "--vault", VAULT, "--archive"])
    return {"ok": True, "items": items()}


# ── rotate ─────────────────────────────────────────────────────────────────────────────────────────
def _value(iid: str, field: str = "credential") -> str:
    return _op(["read", f"op://{VAULT}/{iid}/{field or 'credential'}", "--no-newline"])


def _http(method: str, url: str, token: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json", "User-Agent": "Tabbie/1"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310 (fixed provider hosts)
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        try:
            j = json.loads(e.read() or b"{}")
        except ValueError:
            j = {}
        msg = j.get("message") or ((j.get("errors") or [{}])[0].get("message") if j.get("errors") else "") or e.reason
        raise SecretError(f"{e.code}: {str(msg)[:160]}") from None
    except urllib.error.URLError:
        raise SecretError("Couldn't reach the provider.") from None


def _rotate_resend(old: str, key_id: str) -> tuple[str, str, str]:
    made = _http("POST", "https://api.resend.com/api-keys", old,
                 {"name": f"Tabbie {time.strftime('%Y-%m-%d %H:%M')}", "permission": "full_access"})
    new, new_id = made.get("token") or "", made.get("id") or ""
    if not new.startswith("re_"):
        raise SecretError("Resend didn't return a new key (rotating needs a full-access key).")
    note = ""
    if key_id:
        try:
            _http("DELETE", f"https://api.resend.com/api-keys/{key_id}", new)
            note = "The old key was deleted in Resend."
        except SecretError as exc:
            note = f"The old key couldn't be deleted ({exc}); delete it in Resend → API Keys."
    else:
        note = "Delete the old key in Resend → API Keys (Tabbie didn't know its id; it will next time)."
    return new, new_id, note


def _rotate_cloudflare(old: str, key_id: str) -> tuple[str, str, str]:
    tid = key_id or (_http("GET", "https://api.cloudflare.com/client/v4/user/tokens/verify", old).get("result") or {}).get("id")
    if not tid:
        raise SecretError("Cloudflare didn't say which token this is.")
    try:
        res = _http("PUT", f"https://api.cloudflare.com/client/v4/user/tokens/{tid}/value", old, {})
    except SecretError as exc:
        raise SecretError(f"Cloudflare refused ({exc}). The token needs “API Tokens: Edit” to roll itself; "
                          "or roll it in the Cloudflare dashboard and paste the new value here.") from None
    new = res.get("result") if isinstance(res.get("result"), str) else ""
    if not new:
        raise SecretError("Cloudflare didn't return a new value.")
    return new, tid, "The old value stopped working the moment it rolled."


ROTATORS = {"resend": _rotate_resend, "cloudflare": _rotate_cloudflare}


def rotate(body: dict) -> dict:
    """New key from the provider -> saved to 1Password -> returned ONCE so the user can copy it."""
    iid = str(body.get("id") or "")
    row = next((r for r in items() if r["id"] == iid), None)
    if not row:
        raise SecretError("That secret is gone.")
    fn = ROTATORS.get(row["provider"])
    if not fn:
        raise SecretError(f"Tabbie can't rotate {row['provider_label']} keys yet: make a new one with the provider and paste it with Replace.")
    old = _value(iid, row.get("field") or "credential")
    key_id = _meta(iid).get("key_id", "")
    new, new_id, note = fn(old, key_id)
    old = None
    saved = True
    try:
        _set_value(iid, new, new_id)
    except SecretError:
        saved = False
    INDEX.unlink(missing_ok=True)
    return {"ok": True, "value": new, "saved": saved, "note": note if saved else
            "The provider made the new key but saving it to 1Password failed: copy it now and paste it with Replace."}
