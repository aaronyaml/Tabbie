"""hub for Windows (bin/hub is the macOS one; bin\\hub.cmd runs this).
    hub              open Tabbie (https://hermes.local)
    hub cli          Hermes in the terminal
    hub health       check every part
    hub restart      start anything missing, then restart the gateway and every service
    hub services     start the background services if they aren't running
    hub logs [name]  webui | gateway | caddy | mdns | sidecar | supervisor | dreamer | decisions
    hub import       import Claude project folders into Hermes + WebUI spaces
    hub limits       usage limits
    hub update       check GitHub for a newer Tabbie, and download it if you say yes
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from hmem import winsvc  # noqa: E402
from hmem.paths import hermes_home  # noqa: E402

os.environ["PATH"] = os.pathsep.join([str(hermes_home() / "bin"), str(Path.home() / ".local" / "bin"),
                                      os.environ.get("PATH", "")])
G, R, Y, N = "\033[32m", "\033[31m", "\033[33m", "\033[0m"
if sys.platform == "win32":
    os.system("")  # turns on ANSI colours in the Windows console


def _cc():
    """The command-centre package's submodules (the Hermes plugin itself isn't loaded)."""
    pkg = ROOT / "plugin" / "command-centre"
    spec = importlib.util.spec_from_file_location("cc", pkg / "__init__.py", submodule_search_locations=[str(pkg)])
    sys.modules["cc"] = importlib.util.module_from_spec(spec)
    return importlib.import_module("cc.updates")


def _out(cmd: list[str], timeout: int = 20) -> tuple[int, str]:
    exe = shutil.which(cmd[0])
    if not exe:
        return 127, ""
    try:
        r = subprocess.run([exe, *cmd[1:]], capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, stdin=subprocess.DEVNULL)
        return r.returncode, r.stdout + r.stderr
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def _http(url: str) -> int:
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE  # the check is "is it up", not the certificate
    try:
        with urllib.request.urlopen(url, timeout=5, context=ctx) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except OSError:
        return 0


def check(label: str, ok: bool, fix: str = "", optional: bool = False) -> bool:
    if ok:
        print(f"  {G}✓{N} {label}")
    elif optional:
        print(f"  {Y}!{N} {label} (optional){' — ' + fix if fix else ''}")
    else:
        print(f"  {R}✗{N} {label}{' — ' + fix if fix else ''}")
    return ok


def health() -> int:
    print("Tabbie")
    st = winsvc.status()
    check("Background services (supervisor)", st.get("supervisor", False), "hub services")
    check("Hermes gateway", bool(re.search(r"running|supervised", _out(["hermes", "gateway", "status"])[1])),
          "hermes gateway install")
    check("Hermes WebUI", _http("http://127.0.0.1:8787/health") == 200, "hub logs webui")
    check("HTTPS proxy (Caddy)", st.get("caddy", False), "hub logs caddy")
    check("Tabbie helper (cc-sidecar)", st.get("sidecar", False), "hub logs sidecar")
    check("hermes.local (mDNS)", st.get("mdns", False), "hub logs mdns", optional=True)
    check("WebUI via hermes.local", _http("https://hermes.local/login") == 200, optional=True)
    plugins = _out(["hermes", "plugins", "list"])[1]
    for p in ("command-centre", "fragment-memory", "limits", "workboard"):
        check(f"Plugin {p} enabled", bool(re.search(rf"^. {re.escape(p)} .*enabled", plugins, re.M)),
              f"hermes plugins enable {p}, then hub restart")
    from hmem import agents
    on = agents.enabled()
    if "claude" in on:
        check("Claude (subscription login)", '"authMethod": "claude.ai"' in _out(["claude", "auth", "status"])[1],
              "claude auth login")
    if "codex" in on:
        check("OpenAI/Codex (ChatGPT)", "ChatGPT" in _out(["codex", "login", "status"])[1], "codex login")
    check("Hermes hub signed in", bool(re.search(r"oauth|api|token", _out(["hermes", "auth", "list"])[1], re.I)),
          "hermes setup")
    check("Motion: Node.js", bool(shutil.which("node")), "bin\\setup.ps1 (Motion step)", optional=True)
    check("Attachments store", os.access(ROOT / "state", os.W_OK))
    return 0


def logs(name: str) -> int:
    files = {"webui": hermes_home() / "webui.log", "gateway": hermes_home() / "logs" / "gateway.log",
             "caddy": ROOT / "state" / "logs" / "caddy-server.log", "mdns": ROOT / "state" / "logs" / "mdns.log",
             "sidecar": ROOT / "state" / "logs" / "cc-sidecar.log", "cc-sidecar": ROOT / "state" / "logs" / "cc-sidecar.log",
             "supervisor": ROOT / "state" / "logs" / "supervisor.log", "dreamer": ROOT / "state" / "logs" / "dreamer.log",
             "decisions": ROOT / "state" / "decisions.jsonl"}
    f = files.get(name)
    if name == "caddy" and not (f and f.exists()):
        f = ROOT / "state" / "logs" / "caddy.log"
    if not f:
        print(__doc__)
        return 2
    try:
        print("\n".join(f.read_text(encoding="utf-8", errors="replace").splitlines()[-60:]))
    except OSError:
        print(f"No log yet ({f})")
    return 0


def update() -> int:
    u = _cc()
    got = u.ensure_services()
    if got:
        print("Started: " + ", ".join(got))
    ask = lambda q: sys.stdin.isatty() and input(f"{q} [y/N] ").strip().lower().startswith("y")  # noqa: E731
    if u.pending_restart():
        print("An update was downloaded but its services haven't restarted yet.")
    else:
        r = u.check(force=True)
        if not r["available"]:
            print(r["note"] or f"Tabbie is up to date ({r['local'][:7]}).")
            return 0
        n = r["ahead_by"] or len(r["commits"])
        print(f"Tabbie update available{f': {n} change' + ('s' if n != 1 else '') if n else ''}")
        for c in r["commits"][:10]:
            print(f"  • {c['title']}")
        if not ask("Download it now? Your settings and edited files stay as they are."):
            print("Skipped.")
            return 0
        res = u.apply()
        if not res["ok"]:
            print(res["error"])
            return 1
        print(f"Updated {res['from']} → {res['to']} ({res['files']} files).")
        if "setup" in res["needs"]:
            print("This update changed setup steps: run bin\\setup.ps1 when convenient.")
        if "reload" in res["needs"]:
            print("Reload the WebUI in your browser to get the new interface.")
    if u.pending_restart():
        if not ask("Restart Tabbie's services now to finish? (waits if a request is running)"):
            print("Later: hub update")
            return 0
        sidecar = lambda: winsvc.request_restart(["sidecar"])  # noqa: E731
        if u.busy():
            st = u._state()
            st["restart_when_idle"] = True
            u._save_state(st)
            sidecar()  # it restarts the rest once Tabbie is idle (updates.resume)
            print("Something is running (a request, a worker or a Motion render). Tabbie restarts by itself once it's idle.")
            return 0
        need = u.pending_restart()
        res = u.restart(after_reply=sidecar)
        print("Restarted: " + ", ".join(res.get("restarted") or need) if res["ok"] else res["error"])
    return 0


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "open"
    if cmd == "open":
        os.startfile("https://hermes.local")  # type: ignore[attr-defined]
    elif cmd == "cli":
        return subprocess.call([shutil.which("hermes") or "hermes", *argv[1:]])
    elif cmd == "health":
        return health()
    elif cmd == "services":
        print("Started the background services." if winsvc.installed() and winsvc.start()
              else "The background services are running." if winsvc.running()
              else "Not installed yet: run bin\\setup.ps1 (Background services).")
    elif cmd == "restart":
        if not winsvc.running() and winsvc.installed():
            winsvc.start()
        _out(["hermes", "gateway", "restart"], timeout=120)
        winsvc.request_restart(list(winsvc.NAMES))
        print("restarted")
    elif cmd == "logs":
        return logs(argv[1] if len(argv) > 1 else "webui")
    elif cmd == "import":
        uv = shutil.which("uv")
        return subprocess.call([uv, "run", "--no-project", "-q", "--with", "ruamel.yaml", "python",
                                str(ROOT / "bin" / "cc-import-projects"), *argv[1:]] if uv else
                               [sys.executable, str(ROOT / "bin" / "cc-import-projects"), *argv[1:]])
    elif cmd == "limits":
        return subprocess.call([sys.executable, str(ROOT / "plugin" / "limits" / "__init__.py"), *argv[1:]])
    elif cmd == "update":
        return update()
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
