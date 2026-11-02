"""Secret scrubbing (traces, proposals, memory) and memory-poisoning detection.

Memory is a security boundary: anything persisted here is shown to future agents. Two controls:
- scrub(): removes credentials before anything is written (traces included).
- scan(): flags text that tries to act as instructions to future agents, exfiltrate data, or run
  code. A finding at severity "block" stops a proposal from being applied until a human edits it.
"""

from __future__ import annotations

import re
import unicodedata

REDACTED = "[REDACTED]"

_SECRET_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), REDACTED),
    (re.compile(r"\b(?:sk-ant-[A-Za-z0-9_-]{10,}|sk-[A-Za-z0-9_-]{20,}|sk_(?:live|test)_[A-Za-z0-9]{10,})"), REDACTED),
    (re.compile(r"\b(?:sk|pk|rk|whsec)_[A-Za-z0-9]{20,}"), REDACTED),  # ElevenLabs/Stripe-style keys
    (re.compile(r"\b(?:sk-proj-|sk-or-|xai-|gsk_|hf_|r8_|glpat-|npm_|pypi-|1//)[A-Za-z0-9_-]{16,}"), REDACTED),
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"), REDACTED),
    (re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"), REDACTED),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), REDACTED),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"), REDACTED),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), REDACTED),  # JWT
    (re.compile(r"\bya29\.[A-Za-z0-9_-]{20,}"), REDACTED),  # Google OAuth access token
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{16,}"), r"\1 " + REDACTED),
    (re.compile(r"(://[^/\s:@]+:)[^@\s/]{3,}@"), r"\1" + REDACTED + "@"),  # user:pass@host
]
# KEY=value / "key": "value" where the key name says it's a secret
_KV = re.compile(
    r"(?i)\b([A-Z0-9_]{0,40}?(?:API[_-]?KEY|SECRET|TOKEN|PASSWORD|PASSWD|PRIVATE[_-]?KEY|ACCESS[_-]?KEY|"
    r"CLIENT[_-]?SECRET|AUTH|COOKIE|SESSION[_-]?KEY)[A-Z0-9_]{0,40})(\"?\s{0,3}[:=]\s{0,3}\"?)([^\s\"',;]{6,400})")
_QS = re.compile(r"(?i)([?&](?:token|key|api_key|apikey|access_token|secret|sig|signature|password)=)[^&\s\"']+")

_CRED_PATH = re.compile(
    r"(?i)(^|/)(\.env(\.[\w-]+)?|\.npmrc|\.netrc|\.pypirc|credentials?(\.json)?|auth\.json|"
    r"id_(rsa|ed25519|ecdsa)|[\w.-]*\.(pem|key|p12|pfx)|\.git-credentials|mcp-tokens|keychain[\w.-]*|"
    r"[\w.-]*token[\w.-]*|[\w.-]*secret[\w.-]*)$")


MAX_SCRUB = 200_000
_ENTROPY = re.compile(r"(?<![A-Za-z0-9/+_=-])[A-Za-z0-9+/_=-]{32,200}(?![A-Za-z0-9/+_=-])")


def _looks_random(tok: str) -> bool:
    """Long tokens mixing upper, lower and digits (API keys, base64 secrets). Paths, words, hex SHAs and
    UUIDs don't qualify."""
    if "/" in tok and tok.count("/") > 2:
        return False
    up, lo, dg = (sum(c.isupper() for c in tok), sum(c.islower() for c in tok), sum(c.isdigit() for c in tok))
    return up >= 4 and lo >= 4 and dg >= 3 and not re.fullmatch(r"[A-Za-z]+(?:[_-][A-Za-z]+)*", tok)


def scrub(text: str) -> str:
    """Patterns are bounded (no unbounded repeats) so huge inputs stay linear; inputs beyond MAX_SCRUB
    are cut first (callers that keep only a short excerpt window before calling, see trace.clean)."""
    if not text:
        return text
    text = text[:MAX_SCRUB]
    out = text
    for rx, repl in _SECRET_PATTERNS:
        out = rx.sub(repl, out)
    out = _ENTROPY.sub(lambda m: REDACTED if _looks_random(m.group(0)) else m.group(0), out)
    out = _KV.sub(lambda m: m.group(1) + m.group(2) + REDACTED, out)
    out = _QS.sub(lambda m: m.group(1) + REDACTED, out)
    return out


def scrub_obj(o):
    if isinstance(o, str):
        return scrub(o)
    if isinstance(o, list):
        return [scrub_obj(x) for x in o]
    if isinstance(o, dict):
        return {k: (REDACTED if _KV.fullmatch(f"{k}=xxxxxxxx") and isinstance(v, str) else scrub_obj(v))
                for k, v in o.items()}
    return o


def is_credential_path(path: str) -> bool:
    p = str(path or "").replace("\\", "/").rstrip("/")  # Windows separators too
    return bool(_CRED_PATH.search(p)) or "/.ssh/" in p or "/Keychains/" in p or "/.aws/" in p \
        or "/.config/gh/" in p or "/.codex/auth" in p or "/.claude/.credentials" in p


# ------------------------------------------------------------------ poisoning

_RULES: list[tuple[str, str, re.Pattern]] = [
    ("block", "override instructions", re.compile(
        r"(?i)\b(ignore|disregard|forget|override)\b[^.\n]{0,40}\b(previous|prior|above|earlier|all|system|"
        r"safety|your)\b[^.\n]{0,20}\b(instructions?|rules?|prompts?|guidelines?|messages?)")),
    ("block", "role hijack", re.compile(
        r"(?i)(\byou are now\b|\bnew (system )?instructions?\b|\bsystem prompt\b|<\/?(system|assistant|"
        r"instructions?)>|\[\s*(system|INST)\s*\]|\bdeveloper mode\b|\bjailbreak)")),
    ("block", "secrecy / concealment", re.compile(
        r"(?i)\b(do not|don't|never)\b[^.\n]{0,30}\b(tell|inform|mention|reveal|show)\b[^.\n]{0,20}\b(the )?"
        r"(user|human|owner)")),
    ("block", "exfiltration", re.compile(
        r"(?i)\b(send|post|upload|exfiltrate|forward|transmit|email|leak)\b[^\n]{0,80}(\b(token|key|secret|"
        r"password|credential|cookie|ssh|keychain)|\.env\b|auth\.json)")),
    ("block", "remote code execution", re.compile(
        r"(?i)(curl|wget|iwr|invoke-webrequest)\b[^|\n]{0,200}\|\s*(ba|z)?sh\b|\bbase64\s+(-d|--decode)\b[^\n]{0,80}\|"
        r"\s*(ba|z)?sh|\beval\s*\(\s*(atob|base64)")),
    ("block", "disable safety", re.compile(
        r"(?i)(--dangerously-skip-permissions|bypassPermissions|disable (the )?(sandbox|safety|security|"
        r"approval)|chmod\s+777\s+/|rm\s+-rf\s+(~|/|\$HOME)(\s|$))")),
    ("warn", "standing order", re.compile(
        r"(?i)\b(always|from now on|in (all|every) future|every time)\b[^.\n]{0,60}\b(run|execute|download|"
        r"install|visit|open|call|fetch|send)\b")),
    ("warn", "external url", re.compile(r"(?i)\bhttps?://(?!localhost|127\.0\.0\.1|hermes\.local|"
                                        r"docs\.anthropic\.com|code\.claude\.com)[^\s)>\"']+")),
]
_HIDDEN = re.compile("[​-‏‪-‮⁠-⁤﻿\U000e0000-\U000e007f]")


def scan(text: str) -> list[dict]:
    """Poisoning findings: [{severity, rule, match}]."""
    text = text or ""
    found = []
    if _HIDDEN.search(text):
        found.append({"severity": "block", "rule": "hidden unicode", "match": "invisible/bidi/tag characters"})
    norm = unicodedata.normalize("NFKC", _HIDDEN.sub("", text))
    for sev, name, rx in _RULES:
        m = rx.search(norm)
        if m:
            found.append({"severity": sev, "rule": name, "match": m.group(0)[:120]})
    if scrub(norm) != norm:
        found.append({"severity": "block", "rule": "secret", "match": "credential-like value"})
    return found


def blocked(findings: list[dict]) -> bool:
    return any(f["severity"] == "block" for f in findings)


def quote_for_context(title: str, body: str, meta_line: str = "") -> str:
    """How memory is shown to agents: as reference data, never as instructions."""
    q = "\n".join("> " + ln for ln in body.strip().splitlines())
    return f"[memory: {title}{' · ' + meta_line if meta_line else ''}]\n{q}"


def scan_fields(meta: dict, body: str) -> list[dict]:
    """Scan every field a future agent will see, not just the body."""
    parts = [str(meta.get(k) or "") for k in ("title", "description", "id")]
    parts += [str(x) for k in ("tags", "refs", "evidence") for x in (meta.get(k) or [])]
    return scan("\n".join(parts + [body]))


def safe_ref(ref: str) -> bool:
    """refs point at code a memory describes. Staleness checks only test existence, never read them."""
    import os
    if not isinstance(ref, str) or not ref or any(c in ref for c in "\n\r\0`$|;&<>"):
        return False
    home = os.path.normcase(os.path.realpath(os.path.expanduser("~")))
    p = os.path.normcase(os.path.realpath(os.path.expanduser(ref)))
    rest = p[len(home):].replace("\\", "/")
    return p.startswith(home + os.sep) and not is_credential_path(p) and "/." not in rest.replace("/.claude/skills", "")


_TITLE_OK = re.compile(r"[^\w .,:;()'/+-]")


def clean_title(t: str, n: int = 80) -> str:
    """Titles go into prompts unquoted-ish: printable, short, no URLs/backticks/newlines."""
    t = re.sub(r"https?://\S+", "", unicodedata.normalize("NFKC", _HIDDEN.sub("", str(t or ""))))
    return _TITLE_OK.sub("", t.replace("\n", " ")).strip()[:n]
