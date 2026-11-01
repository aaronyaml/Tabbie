#!/usr/bin/env python3
"""Tabbie setup page: the same steps as bin/setup-terminal, as a page in your browser.

bin/setup starts this on 127.0.0.1 (a random free port) and opens the page with a one-time token.
Only this Mac can reach it: it binds to loopback, checks the Host header (no DNS rebinding), and
every request needs the token cookie; POSTs also need a same-origin Origin and an X-Tabbie header.
Every action is from a fixed list (no command ever comes from the page), and installs/sign-ins run
the providers' own official commands. Stdlib only: runs on the Mac's built-in Python 3.9.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
HOME = Path.home()
TOKEN = secrets.token_urlsafe(24)
PAGE = Path(__file__).with_name("page.html")
BRAND = {"tabbie.png": ROOT / "brand" / "avatar" / "current.png",
         "favicon.svg": ROOT / "brand" / "icons" / "favicon.svg"}
ENV_FILE = ROOT / "hermes-webui" / ".env"
CADDY_CA = HOME / "Library/Application Support/Caddy/pki/authorities/local/root.crt"
TS_APP = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
os.environ["PATH"] = os.pathsep.join([str(HOME / ".hermes/bin"), str(HOME / ".local/bin"), "/opt/homebrew/bin",
                                      "/usr/local/bin", os.environ.get("PATH", "")])
sys.path.insert(0, str(ROOT))
from hmem import agents as agent_set  # noqa: E402

# Tools install with bin/install-tool: official sources, the right build for Apple silicon or Intel,
# into ~/.local/bin. No Homebrew and no Mac password. Only Apple's developer tools (git, python3)
# come from Apple's own installer window.
TOOLS = ("hermes", "claude", "codex", "caddy", "uv")
HUB_MODEL = {"openai-codex": "gpt-6.1-sol", "anthropic": "claude-sonnet-5-5"}


def have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def sh(cmd: list, timeout: int = 20) -> tuple:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)


def tailscale() -> list:
    for c in ("tailscale", TS_APP):
        if have(c) or os.access(c, os.X_OK):
            sock = ROOT / "state" / "tailscale" / "tailscaled.sock"
            return [c, "--socket", str(sock)] if sock.exists() else [c]
    return []


# ── state ────────────────────────────────────────────────────────────────────────────────────────
def _name():
    try:
        m = re.search(r'^\s*name:\s*"(.*)",\s*$', (ROOT / "webui-extension" / "local.js").read_text(), re.M)
        return json.loads(f'"{m.group(1)}"') if m else ""
    except (OSError, ValueError):
        return None


def state() -> dict:
    chosen = agent_set.path().exists()
    on = agent_set.enabled()
    hub = sh(["hermes", "config", "get", "model.provider"])[1].strip().splitlines()[-1:] if have("hermes") else []
    hub = hub[0].strip() if hub else ""
    checks = {
        "claude_in": lambda: have("claude") and "claude.ai" in sh(["claude", "auth", "status"])[1],
        "codex_in": lambda: have("codex") and "ChatGPT" in sh(["codex", "login", "status"])[1],
        "hub_in": lambda: have("hermes") and bool(hub) and re.search(
            rf"^{re.escape(hub)} .*\n(?:\s+.*\n)*?\s+#\d+\s+\S+\s+oauth", sh(["hermes", "auth", "list"])[1] + "\n", re.M) is not None,
        "services": lambda: sh(["launchctl", "list", "ai.hermes.webui"])[0] == 0,
        "trusted": lambda: CADDY_CA.exists() and sh(["security", "verify-cert", "-c", str(CADDY_CA)])[0] == 0,
        "git": lambda: sh(["xcode-select", "-p"])[0] == 0 and have("git"),
    }
    ts = tailscale()
    if ts:
        checks["ts_on"] = lambda: sh(ts + ["status"])[0] == 0
        checks["ts_served"] = lambda: "127.0.0.1:8788" in sh(ts + ["serve", "status"])[1] or "127.0.0.1:8787" in sh(ts + ["serve", "status"])[1]
    with ThreadPoolExecutor(max_workers=len(checks)) as ex:
        futs = {k: ex.submit(fn) for k, fn in checks.items()}
        got = {k: bool(f.result()) for k, f in futs.items()}
    ts_url = ""
    if got.get("ts_on"):
        try:
            ts_url = "https://" + json.loads(sh(ts + ["status", "--json"])[1])["Self"]["DNSName"].rstrip(".")
        except (ValueError, KeyError):
            pass
    env = ENV_FILE.read_text() if ENV_FILE.exists() else ""
    files = all([(ROOT / "webui-extension/local.js").exists(), (ROOT / "config/projects.yaml").exists(), ENV_FILE.exists(),
                 (HOME / ".hermes/plugins/command-centre").exists(), (ROOT / "memory").exists()])
    tools = {"git": got["git"], "hermes": have("hermes"), "claude": have("claude"), "codex": have("codex"),
             "caddy": have("caddy"), "uv": have("uv")}
    return {
        "name": _name(), "agents": on if chosen else None, "hub": hub,
        "tools": tools,
        "signin": {"hub": got["hub_in"], "claude": got["claude_in"], "codex": got["codex_in"]},
        "files": files,
        "password": bool(re.search(r"^HERMES_WEBUI_PASSWORD=.+", env, re.M)),
        "services": got["services"],
        "cert": {"caddy": have("caddy"), "exists": CADDY_CA.exists(), "trusted": got["trusted"]},
        "tailscale": {"installed": bool(ts), "connected": got.get("ts_on", False), "served": got.get("ts_served", False),
                      "url": ts_url},
        "open_url": "https://hermes.local" if have("caddy") else "http://127.0.0.1:8787",
    }


# ── jobs ─────────────────────────────────────────────────────────────────────────────────────────
JOBS: dict = {}
_LOCK = threading.Lock()


def job(cmd, label: str) -> str:
    """Run a fixed command in the background; the page polls its output."""
    jid = secrets.token_hex(6)
    j = {"label": label, "lines": [], "done": False, "ok": None, "started": time.time()}
    JOBS[jid] = j

    def run():
        try:
            p = subprocess.Popen(cmd, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, text=True, bufsize=1,
                                 env={**os.environ, "NONINTERACTIVE": "1", "CI": "", "TERM": "dumb"})
            for line in p.stdout:
                line = ANSI.sub("", line.rstrip("\n")).replace("\r", "")
                if line.strip():
                    with _LOCK:
                        j["lines"] = (j["lines"] + [line[:400]])[-300:]
            j["ok"] = p.wait() == 0
        except OSError as exc:
            j["lines"].append(str(exc)); j["ok"] = False
        j["done"] = True
    threading.Thread(target=run, daemon=True).start()
    return jid


def in_terminal(command: str) -> dict:
    """For steps that need your Mac password or typing: open Terminal with the command ready."""
    script = f"cd {shlex.quote(str(ROOT))} && {command}"
    esc = script.replace("\\", "\\\\").replace('"', '\\"')
    sh(["osascript", "-e", f'tell application "Terminal" to do script "{esc}"', "-e", 'tell application "Terminal" to activate'])
    return {"ok": True, "terminal": True}


def write_name(name: str) -> None:
    name = " ".join(str(name or "").split())[:40]
    js = ("// Per-machine settings (git-ignored). Regenerate with bin/setup.\nwindow.HX_LOCAL = {\n"
          f"  home: {json.dumps(str(HOME))},\n  hermes: {json.dumps(str(ROOT))},\n"
          f"  motion: {json.dumps(str(HOME / 'motion'))},\n  name: {json.dumps(name, ensure_ascii=False)},\n}};\n")
    (ROOT / "webui-extension" / "local.js").write_text(js)


def write_password(pw: str) -> None:
    if not ENV_FILE.exists():
        raise ValueError("Run step 5 (Local files) first")
    lines = [l for l in ENV_FILE.read_text().splitlines() if not l.startswith("HERMES_WEBUI_PASSWORD=")]
    lines.append("HERMES_WEBUI_PASSWORD=" + pw)
    tmp = ENV_FILE.with_name(".env.tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, ENV_FILE)


def set_hub(hub: str) -> None:
    """Point the hub at the chosen subscription login (Hermes config backed up first)."""
    if not have("hermes") or hub not in HUB_MODEL:
        return
    cur = sh(["hermes", "config", "get", "model.provider"])[1].strip().splitlines()[-1:]
    if cur and cur[0].strip() == hub:
        return
    cfg = sh(["hermes", "config", "path"])[1].strip()
    if cfg and Path(cfg).exists():
        bk = ROOT / "backups" / f"setup-{time.strftime('%Y%m%d-%H%M%S')}"
        bk.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cfg, bk / Path(cfg).name)
    sh(["hermes", "config", "set", "model.provider", hub])
    sh(["hermes", "config", "set", "model.default", HUB_MODEL[hub]])


def agents_arg() -> str:
    return ",".join(agent_set.enabled())


def do(body: dict) -> dict:
    a = str(body.get("action") or "")
    term = bool(body.get("terminal"))
    if a == "name":
        write_name(body.get("name", ""))
        return {"ok": True}
    if a == "agents":
        picked = agent_set.save([x for x in body.get("agents") or [] if isinstance(x, str)])
        hub = body.get("hub") if "codex" in picked and "claude" in picked else ("openai-codex" if "codex" in picked else "anthropic")
        set_hub(str(hub or "openai-codex"))
        return {"ok": True, "agents": picked}
    if a == "install":
        t = str(body.get("tool") or "")
        if t == "git":  # Apple's own installer window (git + python3)
            sh(["xcode-select", "--install"])
            return {"ok": True, "apple": True}
        if t not in TOOLS:
            return {"ok": False, "error": "unknown tool"}
        return {"ok": True, "job": job([str(ROOT / "bin" / "install-tool"), t], f"Installing {t}")}
    if a == "signin":
        who = str(body.get("who") or "")
        hub = sh(["hermes", "config", "get", "model.provider"])[1].strip().splitlines()[-1:] or ["openai-codex"]
        hub = hub[0].strip() if hub[0].strip() in HUB_MODEL else "openai-codex"
        cmds = {"claude": ["claude", "auth", "login", "--claudeai"], "codex": ["codex", "login"],
                "hub": ["hermes", "auth", "add", hub, "--type", "oauth"] + (["--browser"] if hub == "openai-codex" else [])}
        if who not in cmds:
            return {"ok": False, "error": "unknown sign-in"}
        if term:
            return in_terminal(" ".join(shlex.quote(c) for c in cmds[who]))
        return {"ok": True, "job": job(cmds[who], f"Signing in ({who})")}
    if a in ("files", "services", "cert"):
        cmd = ["bin/setup-terminal", "--yes", "--only", a, "--agents", agents_arg()]
        if a == "services":
            cmd.append("--services")
        return {"ok": True, "job": job(cmd, a)}
    if a == "password":
        pw = str(body.get("password") or "")
        if len(pw) < 8:
            return {"ok": False, "error": "At least 8 characters, please."}
        write_password(pw)
        return {"ok": True}
    if a == "ts_install":
        sh(["open", "macappstore://apps.apple.com/app/tailscale/id1475387142"])
        return {"ok": True}
    if a == "ts_dns":
        sh(["open", "https://login.tailscale.com/admin/dns"])
        return {"ok": True}
    if a == "ts_serve":
        ts = tailscale()
        if not ts:
            return {"ok": False, "error": "Tailscale isn't installed yet."}
        return {"ok": True, "job": job(ts + ["serve", "--bg", "8788" if have("caddy") else "8787"], "Sharing on Tailscale")}
    if a == "check":
        return {"ok": True, "job": job(["bin/hub", "health"], "Health check")}
    if a == "open":
        sh(["open", state()["open_url"]])
        return {"ok": True}
    return {"ok": False, "error": "unknown action"}


# ── http ─────────────────────────────────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    server_version = "tabbie-setup/1"

    def log_message(self, *a):
        pass

    def _host_ok(self) -> bool:
        return self.headers.get("Host", "") in (f"127.0.0.1:{PORT}", f"localhost:{PORT}")

    def _authed(self) -> bool:
        c = self.headers.get("Cookie", "")
        return any(p.strip() == f"tabbie_setup={TOKEN}" for p in c.split(";"))

    def _send(self, code: int, body, ctype="application/json", headers=None):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, {"error": "forbidden"})
        u = urlsplit(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path == "/" and q.get("t") and secrets.compare_digest(q["t"], TOKEN):
            return self._send(303, b"", "text/plain", {"Location": "/",
                              "Set-Cookie": f"tabbie_setup={TOKEN}; HttpOnly; SameSite=Strict; Path=/"})
        if not self._authed():
            return self._send(403, b"<p style='font:16px system-ui;padding:2em'>Open the link that bin/setup printed in Terminal.</p>",
                              "text/html; charset=utf-8")
        if u.path == "/":
            return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8",
                              {"Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                                                          "script-src 'self' 'unsafe-inline'; img-src 'self' data:"})
        if u.path.startswith("/brand/") and u.path[7:] in BRAND and BRAND[u.path[7:]].exists():
            f = BRAND[u.path[7:]]
            return self._send(200, f.read_bytes(), "image/svg+xml" if f.suffix == ".svg" else "image/png")
        if u.path == "/api/state":
            return self._send(200, state())
        if u.path == "/api/job":
            j = JOBS.get(q.get("id", ""))
            if not j:
                return self._send(404, {"error": "no such job"})
            with _LOCK:
                return self._send(200, {k: j[k] for k in ("label", "lines", "done", "ok")})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._host_ok() or not self._authed() or self.headers.get("X-Tabbie") != "1" \
                or self.headers.get("Origin") not in (f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"):
            return self._send(403, {"error": "forbidden"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n < 0 or n > 8192:
                return self._send(413, {"error": "too large"})
            body = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("bad body")
        except ValueError:
            return self._send(400, {"error": "bad json"})
        if urlsplit(self.path).path == "/api/finish":
            self._send(200, {"ok": True})
            threading.Thread(target=lambda: (time.sleep(0.5), os._exit(0)), daemon=True).start()
            return
        if urlsplit(self.path).path != "/api/do":
            return self._send(404, {"error": "not found"})
        try:
            res = do(body)
        except Exception as exc:  # show the reason, never a traceback
            res = {"ok": False, "error": str(exc)[:200]}
        return self._send(200 if res.get("ok") else 400, res)


def main():
    global PORT
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    PORT = srv.server_address[1]
    url = f"http://127.0.0.1:{PORT}/?t={TOKEN}"
    print(f"\n  Tabbie setup is open in your browser.\n  If it didn't open, paste this link into it:\n\n    {url}\n\n"
          "  Leave this window open until setup says you're done (Ctrl-C to stop).\n", flush=True)
    if "--no-open" not in sys.argv:
        webbrowser.open(url)
    # stop on its own after 3 quiet hours
    threading.Thread(target=lambda: (time.sleep(3 * 3600), os._exit(0)), daemon=True).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


PORT = 0
if __name__ == "__main__":
    main()
