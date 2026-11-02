"""Tabbie setup on Windows (bin/setup.ps1 runs this; bin/setup-terminal is the macOS version).

Walks through every step in order, asks before installing or changing anything, and is safe to re-run.
    setup.ps1                                 every step
    setup.ps1 --yes                           no questions: defaults; sign-ins and password left for later
    setup.ps1 --agents claude,codex --name Sam --services
    setup.ps1 --only files,services           just those steps
Steps: name agents tools signin files password services cert motion phone check.
Nothing here asks for or stores an API key: every provider signs in with its own login.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
from hmem.paths import hermes_home, link_dir, is_link, unlink  # noqa: E402

HOME = Path.home()
LOCAL_BIN = HOME / ".local" / "bin"
PROGRAMS = Path(os.environ.get("LOCALAPPDATA", HOME / "AppData" / "Local")) / "Programs"
ENVF = ROOT / "hermes-webui" / ".env"
ARCH = "aarch64" if os.environ.get("PROCESSOR_ARCHITECTURE", "").upper() == "ARM64" else "x86_64"
PLUGINS = ("command-centre", "fragment-memory", "limits", "workboard")
STEPS = ("name", "agents", "tools", "signin", "files", "password", "services", "cert", "motion", "phone", "check")

if sys.platform == "win32":
    os.system("")  # ANSI colours in the console
B, D, G, Y, R, C, N = "\033[1m", "\033[2m", "\033[32m", "\033[33m", "\033[31m", "\033[36m", "\033[0m"
ok = lambda m: print(f"  {G}✓{N} {m}")      # noqa: E731
warn = lambda m: print(f"  {Y}!{N} {m}")    # noqa: E731
fail = lambda m: print(f"  {R}✗{N} {m}")    # noqa: E731
say = lambda m: print(f"  {m}")             # noqa: E731
dim = lambda m: print(f"  {D}{m}{N}")       # noqa: E731

A: argparse.Namespace
ASK = True
_n = 0


def step(title: str, why: str = "") -> None:
    global _n
    _n += 1
    total = len(A.only) if A.only else len(STEPS)
    print(f"\n{C}{B}━━ Step {_n} of {total} · {title} {'━' * max(4, 60 - len(title))}{N}")
    if why:
        dim(why)
    print()


def yn(q: str, default: bool = True) -> bool:
    if not ASK:
        return default
    while True:
        a = input(f"  {q} {'[Y/n]' if default else '[y/N]'} ").strip().lower()
        if not a:
            return default
        if a[0] in "yn":
            return a[0] == "y"


def want(s: str) -> bool:
    return not A.only or s in A.only


def refresh_path() -> None:
    """This process's PATH: the user's saved PATH plus Tabbie's tool folders (installers change it)."""
    extra = [str(LOCAL_BIN), str(hermes_home() / "bin"), str(PROGRAMS / "codex" / "bin"), str(PROGRAMS / "node")]
    user = ""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            user = winreg.QueryValueEx(k, "Path")[0]
    except OSError:
        pass
    os.environ["PATH"] = os.pathsep.join(extra + [user, os.environ.get("PATH", "")])


def add_user_path(folder: Path) -> None:
    """Add a folder to the user's PATH (new terminals see it). No admin needed."""
    import ctypes
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
        try:
            cur, kind = winreg.QueryValueEx(k, "Path")
        except OSError:
            cur, kind = "", winreg.REG_EXPAND_SZ
        parts = [p for p in cur.split(";") if p]
        if any(os.path.normcase(p) == os.path.normcase(str(folder)) for p in parts):
            return
        winreg.SetValueEx(k, "Path", 0, kind, ";".join(parts + [str(folder)]))
    ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x1A, 0, "Environment", 2, 5000, None)  # WM_SETTINGCHANGE


def have(t: str) -> str | None:
    return shutil.which(t)


def out(cmd: list[str], timeout: int = 60) -> tuple[int, str]:
    exe = have(cmd[0])
    if not exe:
        return 127, ""
    try:
        r = subprocess.run([exe, *cmd[1:]], capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, stdin=subprocess.DEVNULL)
        return r.returncode, r.stdout + r.stderr
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def run(cmd: list[str]) -> int:
    print(f"  {D}$ {' '.join(cmd)}{N}")
    exe = have(cmd[0]) or cmd[0]
    try:
        return subprocess.call([exe, *cmd[1:]])
    except OSError as exc:
        fail(str(exc))
        return 1


def ps(script: str) -> int:
    """An official PowerShell installer one-liner."""
    print(f"  {D}$ {script}{N}")
    return subprocess.call(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script])


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=600) as r:
        return r.read()


def latest_tag(repo: str) -> str:
    req = urllib.request.Request(f"https://github.com/{repo}/releases/latest", method="HEAD")
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.url.rsplit("/tag/", 1)[-1]


def backup_hermes_config() -> None:
    if getattr(backup_hermes_config, "done", False):
        return
    code, cfg = out(["hermes", "config", "path"])
    cfg = cfg.strip().splitlines()[-1] if cfg.strip() else ""
    if code == 0 and cfg and Path(cfg).is_file():
        bk = ROOT / "backups" / f"setup-{datetime.now():%Y%m%d-%H%M%S}"
        bk.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cfg, bk)
        dim(f"Hermes config backed up to {bk.relative_to(ROOT)}\\")
        backup_hermes_config.done = True  # type: ignore[attr-defined]


# ── tool installers (official sources only) ───────────────────────────────────────────────────────

def install_codex() -> None:
    """OpenAI's Windows package: codex.exe plus the sandbox helpers it needs beside it."""
    say(f"Downloading Codex for {ARCH}-pc-windows-msvc…")
    data = fetch(f"https://github.com/openai/codex/releases/latest/download/codex-package-{ARCH}-pc-windows-msvc.tar.gz")
    dest = PROGRAMS / "codex"
    new = dest.with_name("codex.new")
    shutil.rmtree(new, ignore_errors=True)
    with tarfile.open(fileobj=io.BytesIO(data)) as tf:
        tf.extractall(new, filter="data")
    shutil.rmtree(dest, ignore_errors=True)
    new.rename(dest)
    add_user_path(dest / "bin")


def install_caddy() -> None:
    tag = latest_tag("caddyserver/caddy")
    v = tag.lstrip("v")
    arch = "arm64" if ARCH == "aarch64" else "amd64"
    say(f"Downloading Caddy {v} for windows_{arch}…")
    data = fetch(f"https://github.com/caddyserver/caddy/releases/download/{tag}/caddy_{v}_windows_{arch}.zip")
    LOCAL_BIN.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        (LOCAL_BIN / "caddy.exe").write_bytes(z.read("caddy.exe"))
    add_user_path(LOCAL_BIN)


def install_node() -> None:
    """Node.js LTS from nodejs.org, checked against its SHASUMS256.txt."""
    index = json.loads(fetch("https://nodejs.org/dist/index.json"))
    v = next(r["version"] for r in index if r.get("lts"))
    arch = "arm64" if ARCH == "aarch64" else "x64"
    f = f"node-{v}-win-{arch}.zip"
    say(f"Downloading Node.js {v} for win-{arch}…")
    data = fetch(f"https://nodejs.org/dist/{v}/{f}")
    sums = fetch(f"https://nodejs.org/dist/{v}/SHASUMS256.txt").decode()
    want_sum = next((ln.split()[0] for ln in sums.splitlines() if ln.endswith(" " + f)), "")
    if hashlib.sha256(data).hexdigest() != want_sum:
        raise RuntimeError("Node download didn't match its checksum")
    dest = PROGRAMS / "node"
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            z.extractall(tmp)
        shutil.rmtree(dest, ignore_errors=True)
        shutil.move(str(Path(tmp) / f[:-4]), str(dest))
    add_user_path(dest)


INSTALL = {
    "hermes": lambda: ps("iex (irm https://hermes-agent.nousresearch.com/install.ps1)"),
    "claude": lambda: ps("irm https://claude.ai/install.ps1 | iex"),
    "uv": lambda: ps("irm https://astral.sh/uv/install.ps1 | iex"),
    "codex": install_codex, "caddy": install_caddy, "node": install_node,
}


def tool(t: str, why: str, required: bool = True) -> bool:
    if have(t):
        ok(f"{t} — {why}")
        return True
    if t in INSTALL and yn(f"{t} is missing ({why}). Install it now?", required):
        try:
            INSTALL[t]()
        except Exception as exc:  # noqa: BLE001  (a failed download says so; setup carries on)
            fail(f"{t}: {exc}")
        refresh_path()
    if have(t):
        ok(f"{t} installed")
        return True
    (fail if required else warn)(f"{t} {'still missing' if required else 'skipped (optional)'} — {why}")
    return False


# ── steps ─────────────────────────────────────────────────────────────────────────────────────────

def s_name() -> None:
    step("About you", 'Your first name, for the greeting on the chat home: "Morning, Sam".')
    from hmem import motion
    cur = re.search(r'^  name: "(.*)",$', (ROOT / "webui-extension" / "local.js").read_text(encoding="utf-8"), re.M) \
        if (ROOT / "webui-extension" / "local.js").exists() else None
    name = A.name
    if name is None:
        guess = cur.group(1) if cur else (os.environ.get("USERNAME", "").split(".")[0].capitalize())
        name = (input(f"  Your first name [{guess or 'skip'}]: ").strip() or guess) if ASK else guess
    name = " ".join(name.split())[:40]
    (ROOT / "webui-extension" / "local.js").write_text(
        "// Per-machine settings (git-ignored). Regenerate with bin\\setup.ps1 (--name to change the greeting).\n"
        "window.HX_LOCAL = {\n"
        f"  home: {json.dumps(str(HOME))},\n  hermes: {json.dumps(str(ROOT))},\n"
        f"  motion: {json.dumps(str(motion.studio_dir()))},\n  name: {json.dumps(name, ensure_ascii=False)},\n}};\n",
        encoding="utf-8")
    ok(f"Greeting: Morning{', ' + name if name else ' (no name)'}")


def s_agents() -> list[str]:
    from hmem import agents
    if want("agents"):
        step("Coding agents", "Who does the work. Change it any time in the WebUI: model picker → Agents.")
    chosen = [a for a in (A.agents or "").split(",") if a]
    if not chosen and agents.path().exists():
        saved = agents.enabled()
        if saved and (not ASK or not want("agents") or yn(f"Keep your saved choice ({', '.join(saved)})?")):
            chosen = saved
    if not chosen:
        if ASK:
            say("1) Claude only         Claude Code does everything. Needs a Claude subscription.")
            say("2) OpenAI only         Codex does everything. Needs a ChatGPT subscription.")
            say("3) Claude and OpenAI   Claude Code does the work, Codex plans big jobs and covers usage limits.")
            while True:
                pick = input("  Pick 1, 2 or 3 [3]: ").strip() or "3"
                if pick in "123":
                    chosen = {"1": ["claude"], "2": ["codex"], "3": ["claude", "codex"]}[pick]
                    break
        else:
            chosen = ["claude", "codex"]
    if not set(chosen) <= {"claude", "codex"}:
        fail("--agents must be claude, codex or claude,codex")
        sys.exit(2)
    if want("agents"):
        saved = agents.save(chosen)
        ok("Using: " + " and ".join({"claude": "Claude (Claude Code)", "codex": "OpenAI (Codex)"}[a] for a in saved))
    return chosen


def s_tools(use: list[str]) -> None:
    step("Tools", "The programs Tabbie runs on. Missing ones can be installed for you, from their official sources.")
    ok(f"Windows ({ARCH})")
    missing = False
    missing |= not tool("git", "version control")
    missing |= not tool("hermes", "Hermes Agent, the hub Tabbie is built on")
    if "claude" in use:
        missing |= not tool("claude", "Claude Code")
    if "codex" in use:
        missing |= not tool("codex", "Codex")
    missing |= not tool("caddy", "serves Tabbie at https://hermes.local, with phone access")
    missing |= not tool("uv", "runs Tabbie's helper service and its Python")
    if missing:
        print()
        fail(f"Some required tools are missing (above). Install them, then run {B}bin\\setup.ps1{N} again.")
        sys.exit(1)


def s_signin(use: list[str]) -> None:
    step("Sign in", "Each provider uses its own login in your browser. No API keys.")

    def signin(label: str, check, login: list[str]) -> None:
        if check():
            ok(f"{label} — signed in")
            return
        if ASK and yn(f"Sign in to {label} now? (opens your browser)"):
            run(login)
            (ok if check() else warn)(f"{label} — {'signed in' if check() else 'not signed in yet. Later: ' + ' '.join(login)}")
        else:
            warn(f"{label} — not signed in. Later: {' '.join(login)}")

    cur = out(["hermes", "config", "get", "model.provider"])[1].strip().splitlines()
    cur = cur[-1].strip() if cur else ""
    if set(use) == {"claude", "codex"}:
        hub = "anthropic" if cur == "anthropic" else "openai-codex"
        if ASK:
            say("The hub chats with you and routes each job. Which login should it use?")
            say("1) ChatGPT (recommended: quick, and leaves your Claude limits for real work)   2) Claude")
            d1 = "2" if hub == "anthropic" else "1"
            hub = "anthropic" if (input(f"  Pick 1 or 2 [{d1}]: ").strip() or d1) == "2" else "openai-codex"
    else:
        hub = "openai-codex" if use == ["codex"] else "anthropic"
    if A.hub in ("openai-codex", "anthropic"):
        hub = A.hub
    label, model = ("ChatGPT", "gpt-6.1-sol") if hub == "openai-codex" else ("Claude", "claude-sonnet-5-5")
    signin(f"Hub on your {label} login",
           lambda: bool(re.search(rf"^{hub} [\s\S]{{0,200}}oauth", out(["hermes", "auth", "list"])[1], re.M)),
           ["hermes", "auth", "add", hub, "--type", "oauth"])
    if cur != hub:
        backup_hermes_config()
        if out(["hermes", "config", "set", "model.provider", hub])[0] == 0 and \
                out(["hermes", "config", "set", "model.default", model])[0] == 0:
            ok(f"Hub model: {model} ({label} login). Change it any time: hermes model")
        else:
            warn("Couldn't set the hub model. Run: hermes model")
    if "claude" in use:
        signin("Claude Code", lambda: '"authMethod": "claude.ai"' in out(["claude", "auth", "status"])[1],
               ["claude", "auth", "login"])
    if "codex" in use:
        signin("Codex (ChatGPT)", lambda: "ChatGPT" in out(["codex", "login", "status"])[1], ["codex", "login"])


def s_files() -> None:
    step("Local files", "Folders and settings on this computer only (none of it is ever committed).")
    for d in ("state/logs", "state/requests", "state/ca", "general", "workspaces"):
        (ROOT / d).mkdir(parents=True, exist_ok=True)
    ok("state\\, general\\, workspaces\\")
    if not (ROOT / "config" / "projects.yaml").exists():
        shutil.copy(ROOT / "config" / "projects.example.yaml", ROOT / "config" / "projects.yaml")
    ok("config\\projects.yaml")
    web = ROOT / "hermes-webui"
    if not web.is_dir():
        if run(["git", "clone", "-q", "https://github.com/nesquena/hermes-webui.git", str(web)]) == 0:
            ok("WebUI downloaded (hermes-webui\\)")
        else:
            fail("Couldn't download the WebUI (check your internet), then re-run")
    else:
        ok("WebUI (hermes-webui\\)")
    if web.is_dir() and not ENVF.exists():
        text = (ROOT / "config" / "webui.env.example").read_text(encoding="utf-8")
        text = text.replace("__HOME__/.hermes", str(hermes_home()).replace("\\", "/")).replace("__HOME__", str(HOME).replace("\\", "/"))
        text = text.replace("hermes/state/requests:", "hermes/state/requests" + os.pathsep)  # list separator on Windows
        ENVF.write_text(text, encoding="utf-8")
    if ENVF.exists():
        if not re.search(r"^HERMES_WEBUI_SKIP_ONBOARDING=", ENVF.read_text(encoding="utf-8"), re.M):
            with open(ENVF, "a", encoding="utf-8") as f:
                f.write("\nHERMES_WEBUI_SKIP_ONBOARDING=1\n")
        ok("hermes-webui\\.env")
    from hmem import store
    store.init()
    ok("Memory store (memory\\, private)")
    soul = hermes_home() / "SOUL.md"
    if "## Command Centre role" not in (soul.read_text(encoding="utf-8") if soul.exists() else ""):
        soul.parent.mkdir(parents=True, exist_ok=True)
        hub_rules = (ROOT / "config" / "SOUL.hub.md").read_text(encoding="utf-8").split("\n", 1)[1]
        with open(soul, "a", encoding="utf-8") as f:
            f.write("\n" + hub_rules)
    ok(f"Hub rules ({soul})")
    plugins = hermes_home() / "plugins"
    plugins.mkdir(parents=True, exist_ok=True)
    for p in PLUGINS:
        link = plugins / p
        if is_link(link):
            unlink(link)
        if not link.exists():
            link_dir(link, ROOT / "plugin" / p)
    ok(f"Plugins linked into {plugins}")
    enabled = out(["hermes", "config", "get", "plugins.enabled"])[1]
    for p in PLUGINS:
        if re.search(rf"^\s*-\s*{re.escape(p)}\s*$", enabled, re.M):
            continue
        backup_hermes_config()
        if out(["hermes", "plugins", "enable", p, "--no-allow-tool-override"])[0] != 0:
            warn(f"Couldn't enable the {p} plugin. Run: hermes plugins enable {p}")
    ok("Plugins enabled (router, memory, limits, kanban/todos)")
    t = re.sub(r"\D", "", out(["hermes", "config", "get", "timeouts.tools.sequential_call"])[1]) or "0"
    if int(t) < 3900:
        backup_hermes_config()
        if out(["hermes", "config", "set", "timeouts.tools.sequential_call", "3900"])[0] == 0:
            ok("Hub waits up to 65 min for workers")
        else:
            warn("Couldn't set the hub timeout. Add timeouts.tools.sequential_call: 3900 to Hermes' config.yaml")
    else:
        ok(f"Hub waits up to {int(t) // 60} min for workers")


def _password_set() -> bool:
    return ENVF.exists() and bool(re.search(r"^HERMES_WEBUI_PASSWORD=.+", ENVF.read_text(encoding="utf-8"), re.M))


def s_password() -> bool:
    step("WebUI password", "The WebUI can start agents on this computer, so it always sits behind a password.")
    if _password_set():
        ok("Password is set")
        if not (ASK and yn("Change it?", False)):
            return False
    if not ASK:
        warn("No password yet. Run bin\\setup.ps1 again (in a terminal) to set one before starting the services.")
        return False
    while True:
        p1 = getpass.getpass("  New password (8+ characters, hidden): ")
        p2 = getpass.getpass("  Same again: ")
        if len(p1) < 8:
            warn("At least 8 characters, please.")
        elif p1 != p2:
            warn("They didn't match. Try again.")
        else:
            break
    lines = [ln for ln in ENVF.read_text(encoding="utf-8").splitlines() if not ln.startswith("HERMES_WEBUI_PASSWORD=")]
    tmp = ENVF.with_suffix(".tmp")
    tmp.write_text("\n".join(lines + ["HERMES_WEBUI_PASSWORD=" + p1]) + "\n", encoding="utf-8")
    os.replace(tmp, ENVF)
    # only this user can read it (the folder's inherited ACL would let other accounts in)
    subprocess.run(["icacls", str(ENVF), "/inheritance:r", "/grant:r", f"{os.environ.get('USERNAME', '')}:F"],
                   capture_output=True)
    ok("Password saved (hermes-webui\\.env, only you can read it)")
    return True


def _lasting_python() -> str | None:
    """A Python that stays put for the sign-in service (uv's managed 3.12, not a throwaway env)."""
    if out(["uv", "python", "install", "3.12"], timeout=600)[0] != 0:
        return None
    code, path = out(["uv", "python", "find", "3.12"])
    path = path.strip().splitlines()[-1] if path.strip() else ""
    return path if code == 0 and Path(path).is_file() else None


def s_services(pw_changed: bool) -> None:
    step("Background services", "Keeps Tabbie running whenever you're signed in: WebUI, hub, HTTPS, Dreamer.")
    from hmem import winsvc
    if winsvc.installed() and winsvc.running():
        ok("Services are installed and running")
        q, d = "Restart them? Only do this when no request is running in Tabbie.", False
        if pw_changed:
            q, d = "Restart them so the new password applies? Only when no request is running.", True
    else:
        q, d = "Install and start them now? (they start when you sign in to Windows; no admin needed)", True
    if not ((not ASK and A.services) or (ASK and yn(q, d or bool(A.services)))):
        dim("Skipped. Later: bin\\setup.ps1 --services")
        return
    if not _password_set():
        fail("Set a WebUI password first (step 6), then run bin\\setup.ps1 again.")
        return
    if winsvc.installed() and winsvc.running():
        winsvc.request_restart(["supervisor"])
        ok("Restarting the services")
    else:
        py = _lasting_python()
        if not py:
            warn("Couldn't install a lasting Python with uv; using this one (re-run setup if services stop starting).")
        winsvc.install(py)
        for _ in range(20):
            if winsvc.running():
                break
            time.sleep(0.5)
        (ok if winsvc.running() else fail)("Background services " + ("running" if winsvc.running() else "didn't start (hub logs supervisor)"))
    (ok if out(["hermes", "gateway", "install"], timeout=180)[0] == 0 else warn)("Hermes gateway")


def s_cert() -> None:
    step("Secure address", "https://hermes.local on your home network, with a padlock.")
    ca = Path(os.environ.get("APPDATA", HOME / "AppData" / "Roaming")) / "Caddy" / "pki" / "authorities" / "local" / "root.crt"
    if not have("caddy"):
        fail("Caddy is missing. Run bin\\setup.ps1 again (Tools step).")
        return
    for _ in range(10):
        if ca.exists():
            break
        time.sleep(1)
    if not ca.exists():
        warn("Caddy hasn't made its certificate yet (is the service running? hub logs caddy). Re-run later.")
        return
    shutil.copy(ca, ROOT / "state" / "ca" / "hermes-ca.crt")
    ok("Phone certificate ready at http://hermes.local/hermes-ca.crt")
    trusted = "Caddy Local Authority" in out(["certutil", "-user", "-store", "Root"])[1]
    if trusted:
        ok("This computer trusts it")
    elif yn("Trust it on this computer (removes browser warnings)? Windows asks you to confirm."):
        if run(["certutil", "-user", "-addstore", "Root", str(ca)]) == 0:
            ok("Trusted on this computer")
        else:
            warn('Not trusted. Later: certutil -user -addstore Root "' + str(ca) + '"')


def s_motion() -> None:
    step("Motion studio", "Optional. Motion (in the WebUI) makes product films with Remotion, which needs Node.js.")
    from hmem import motion
    if ASK and not A.only and not have("node") and not yn(
            "Set up Motion now? (installs Node.js, about 300 MB with the studio's packages)"):
        dim("Skipped. Later: bin\\setup.ps1 --only motion (Motion also sets up its folder the first time it opens)")
        return
    if not have("node"):
        tool("node", "renders Motion films", required=False)
    if not have("node"):
        warn("Node.js didn't install. Later: bin\\setup.ps1 --only motion")
        return
    ok(f"Node.js {out(['node', '-v'])[1].strip()}")
    r = motion.ensure()
    ok(("Studio created: " if r["created"] else "Studio: ") + str(motion.studio_dir()))
    if motion.deps_ready(motion.studio_dir()):
        ok("Studio packages installed")
    else:
        say("Installing the studio's packages (Remotion, about a minute)…")
        (ok if motion.npm_install(wait=True)["state"] == "ready" else warn)("Studio packages installed")


def s_phone() -> None:
    step("Your phone", "Optional. Use Tabbie from your phone, at home or anywhere.")
    say(f"{B}At home (same Wi-Fi):{N} open {B}http://hermes.local/hermes-ca.crt{N} on the phone and install it.")
    say("  iPhone: then Settings › General › About › Certificate Trust Settings → turn it on.")
    say(f"  Then open {B}https://hermes.local{N}. Windows may ask to let Python through the firewall")
    say("  (hermes.local and the HTTPS proxy): allow it on private networks only.")
    print()
    say(f"{B}Away from home:{N} install Tailscale on this computer and the phone (same account), then run:")
    say(f"  {B}tailscale serve --bg 8788{N}   and open this computer's .ts.net address on the phone.")


def s_check() -> None:
    step("Check it all works", "One line per part, with the fix command if something's off.")
    subprocess.call([sys.executable, str(ROOT / "bin" / "hub.py"), "health"])


def main() -> int:
    global A, ASK
    ap = argparse.ArgumentParser(description="Tabbie setup for Windows")
    ap.add_argument("--yes", "-y", action="store_true")
    ap.add_argument("--only", type=lambda s: [x for x in s.split(",") if x])
    ap.add_argument("--hub")
    ap.add_argument("--services", action="store_true")
    ap.add_argument("--agents")
    ap.add_argument("--name")
    A = ap.parse_args()
    ASK = not A.yes and sys.stdin.isatty()
    if sys.platform != "win32":
        print("This is the Windows setup. On a Mac run bin/setup.")
        return 2
    refresh_path()
    if not A.only:
        print(f"\n  {B}Tabbie setup{N}\n  {D}Your coding agents, one inbox — on this computer and your phone.{N}\n")
        say("It asks before installing or changing anything. About 10–15 minutes the first time.")
        say("Safe to run again later: finished steps are ticked and skipped.")
        if ASK:
            input("  Press Enter to start ")
    if want("name"):
        s_name()
    use = s_agents()
    if want("tools"):
        s_tools(use)
    if want("signin"):
        s_signin(use)
    if want("files"):
        s_files()
    pw = s_password() if want("password") else False
    if want("services"):
        s_services(pw)
    if want("cert"):
        s_cert()
    if want("motion"):
        s_motion()
    if want("phone"):
        s_phone()
    if want("check"):
        s_check()
    if not A.only:
        print(f"\n{G}{B}━━ Done {'━' * 70}{N}")
        say(f"Open Tabbie:   {B}https://hermes.local{N}   (or run bin\\hub.cmd)")
        say("Log in with your WebUI password. Anything marked ! above can be fixed by running bin\\setup.ps1 again.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
