"""Tabbie's background services on Windows: what launchd does on macOS.

One small supervisor (bin/tabbie-svc.pyw, started at sign-in from the per-user Run key, no admin
needed) keeps the WebUI, HTTPS proxy, sidecar and hermes.local publisher running, restarting any that
exit, and runs the nightly Dreamer at 03:30 (or on the first wake after it, like launchd).

    tabbie-svc.pyw run                 the supervisor itself (one per user)
    tabbie-svc.pyw install | uninstall start at sign-in (and start now) | stop and remove
    tabbie-svc.pyw restart NAME...     restart services (webui caddy sidecar mdns, or supervisor)
    tabbie-svc.pyw status

Restarts go through a request file the supervisor picks up, never by killing the process tree from
inside it: the WebUI asking for its own restart would otherwise be cut off before it answers.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from . import procutil
from .paths import hermes_home

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "state" / "run"
LOGS = ROOT / "state" / "logs"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE = "Tabbie"
NAMES = ("webui", "caddy", "sidecar", "mdns")
DREAM_AT = (3, 30)
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW: the supervisor is pythonw, children get no console window
_POLL = 2.0


def _exe(name: str, *extra: Path) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for d in (hermes_home() / "bin", Path.home() / ".local" / "bin", *extra):
        for ext in (".exe", ""):
            p = d / (name + ext)
            if p.is_file():
                return str(p)
    return None


def _python() -> str:
    """A console python.exe for the children (pythonw.exe has no stdout to log)."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and (exe.parent / "python.exe").exists():
        return str(exe.parent / "python.exe")
    return str(exe)


def _pythonw(python: str | None = None) -> str:
    exe = Path(python or sys.executable)
    w = exe.parent / "pythonw.exe"
    return str(w) if w.exists() else str(exe)


def env() -> dict:
    e = dict(os.environ)
    home = str(Path.home())
    extra = [str(hermes_home() / "bin"), str(Path.home() / ".local" / "bin")]
    e.update(HOME=home, PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
             PATH=os.pathsep.join(extra + [e.get("PATH", "")]))
    return e


def specs() -> dict[str, dict]:
    """What each service runs. A service whose program isn't installed yet is left out."""
    out: dict[str, dict] = {}
    ps = _exe("powershell") or "powershell"
    webui = ROOT / "hermes-webui"
    if (webui / "start.ps1").exists():  # upstream's native Windows launcher (runs server.py)
        out["webui"] = {"argv": [ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(webui / "start.ps1")],
                        "cwd": webui, "log": hermes_home() / "webui.log"}
    caddy = _exe("caddy")
    if caddy:
        out["caddy"] = {"argv": [caddy, "run", "--config", str(ROOT / "services" / "Caddyfile"), "--adapter", "caddyfile"],
                        "cwd": ROOT, "log": LOGS / "caddy.log"}
    uv = _exe("uv")
    sidecar = str(ROOT / "plugin" / "command-centre" / "sidecar.py")
    out["sidecar"] = {"argv": ([uv, "run", "--no-project", "-q", "--with", "pyyaml", "--with", "ruamel.yaml",
                                "--with", "pywebpush", "python", "-I", sidecar] if uv else [_python(), "-I", sidecar]),
                      "cwd": ROOT, "log": LOGS / "cc-sidecar.log"}
    if uv:  # hermes.local over mDNS (python-zeroconf; dns-sd does this on macOS)
        out["mdns"] = {"argv": [uv, "run", "--no-project", "-q", "--with", "zeroconf", "python", "-I",
                                str(ROOT / "bin" / "tabbie-mdns.py")], "cwd": ROOT, "log": LOGS / "mdns.log"}
    return out


def _pid(name: str) -> int:
    try:
        return int((RUN / f"{name}.pid").read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def _log(msg: str) -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    with open(LOGS / "supervisor.log", "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")


def _start(name: str, spec: dict) -> subprocess.Popen | None:
    log = Path(spec["log"])
    log.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(log, "ab") as out:
            p = subprocess.Popen(spec["argv"], cwd=str(spec["cwd"]), env=env(), stdin=subprocess.DEVNULL,
                                 stdout=out, stderr=subprocess.STDOUT,
                                 creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | NO_WINDOW)  # type: ignore[attr-defined]
    except OSError as exc:
        _log(f"{name}: couldn't start ({exc})")
        return None
    (RUN / f"{name}.pid").write_text(str(p.pid), encoding="utf-8")
    _log(f"{name}: started (pid {p.pid})")
    return p


def _dream_due(now: datetime) -> bool:
    f = RUN / "dreamer.last"
    today = now.strftime("%Y-%m-%d")
    try:
        last = f.read_text(encoding="utf-8").strip()
    except OSError:
        f.write_text(today, encoding="utf-8")  # installed today: the first run is tomorrow night
        return False
    if last == today or (now.hour, now.minute) < DREAM_AT:
        return False
    f.write_text(today, encoding="utf-8")
    return True


def _dream() -> None:
    e = env()
    e["PYTHONPATH"] = str(ROOT) + (os.pathsep + e["PYTHONPATH"] if e.get("PYTHONPATH") else "")
    LOGS.mkdir(parents=True, exist_ok=True)
    with open(LOGS / "dreamer.log", "ab") as out:
        subprocess.Popen([_python(), "-m", "hmem.cli", "dream", "--trigger", "nightly", "--quiet"], cwd=str(ROOT),
                         env=e, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                         creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | NO_WINDOW | 0x4000)  # type: ignore[attr-defined]
    _log("dreamer: nightly run started")


def _requests() -> set[str]:
    """Names in the restart requests that are due: services, "supervisor" (restart it) or "stop"."""
    f = RUN / "restart.json"
    try:
        reqs = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set(), False
    now = time.time()
    due = [r for r in reqs if r.get("at", 0) <= now]
    if not due:
        return set()
    rest = [r for r in reqs if r.get("at", 0) > now]
    if rest:
        f.write_text(json.dumps(rest), encoding="utf-8")
    else:
        f.unlink(missing_ok=True)
    return {n for r in due for n in r.get("names", [])}


def run() -> int:
    """The supervisor loop. Exits at once if another one is running."""
    from . import flockcompat as fcntl
    RUN.mkdir(parents=True, exist_ok=True)
    lock = open(RUN / "supervisor.lock", "a", encoding="utf-8")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 0
    (RUN / "supervisor.pid").write_text(str(os.getpid()), encoding="utf-8")
    _log(f"supervisor: started (pid {os.getpid()})")
    table = specs()
    procs: dict[str, subprocess.Popen] = {}
    wait = {n: 1.0 for n in table}      # backoff before the next start after an exit
    next_at = {n: 0.0 for n in table}
    started = {n: 0.0 for n in table}
    while True:
        names = _requests()
        for n in names & set(procs):
            procutil.stop(procs[n].pid, force=True)
            wait[n], next_at[n] = 1.0, 0.0
        if names & {"supervisor", "stop"}:
            for p in procs.values():
                procutil.stop(p.pid, force=True)
                p.wait(timeout=15)
            if "stop" in names:
                _log("supervisor: stopped")
                lock.close()
                (RUN / "supervisor.pid").unlink(missing_ok=True)
                return 0
            _log("supervisor: restarting")
            lock.close()  # before the new one starts, or it would find the lock taken and quit
            (RUN / "supervisor.pid").unlink(missing_ok=True)
            start()
            return 0
        now = time.time()
        for n, spec in table.items():
            p = procs.get(n)
            if p is not None and p.poll() is None:
                continue
            if p is not None:
                _log(f"{n}: exited ({p.returncode})")
                quick = now - started[n] < 30  # crashing at start: back off up to a minute
                wait[n] = min(wait[n] * 2, 60.0) if quick else 1.0
                next_at[n] = now + wait[n]
                procs.pop(n)
            if now >= next_at[n]:
                q = _start(n, spec)
                if q:
                    procs[n], started[n] = q, now
                else:
                    next_at[n] = now + 60
        if _dream_due(datetime.now()):
            _dream()
        time.sleep(_POLL)


def request_restart(names: list[str], delay: float = 0.0) -> None:
    RUN.mkdir(parents=True, exist_ok=True)
    f = RUN / "restart.json"
    try:
        reqs = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        reqs = []
    reqs.append({"names": list(names), "at": time.time() + delay})
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(reqs), encoding="utf-8")
    os.replace(tmp, f)


def running() -> bool:
    return procutil.alive(_pid("supervisor"))


def _command(python: str | None = None) -> str:
    return f'"{_pythonw(python)}" "{ROOT / "bin" / "tabbie-svc.pyw"}" run'


def installed() -> bool:
    if sys.platform != "win32":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, VALUE)
        return True
    except OSError:
        return False


def _registered_python() -> str | None:
    """The pythonw.exe the Run key starts (setup picks a lasting one, not a throwaway uv env)."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            cmd = winreg.QueryValueEx(k, VALUE)[0]
    except OSError:
        return None
    return cmd.split('"')[1] if cmd.startswith('"') else None


def start() -> bool:
    if running():
        return False
    subprocess.Popen([_registered_python() or _pythonw(), str(ROOT / "bin" / "tabbie-svc.pyw"), "run"], cwd=str(ROOT), env=env(),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     **procutil.DETACHED)
    return True


def install(python: str | None = None) -> bool:
    """Start at sign-in (per-user Run key: no admin, no Task Scheduler) and start now."""
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
        winreg.SetValueEx(k, VALUE, 0, winreg.REG_SZ, _command(python))
    start()
    return True


def uninstall() -> None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, VALUE)
    except OSError:
        pass
    pid = _pid("supervisor")
    if not procutil.alive(pid):
        return
    request_restart(["stop"])
    for _ in range(20):
        if not procutil.alive(pid):
            return
        time.sleep(0.5)
    procutil.stop(pid, force=True)  # taskkill /T: the supervisor and every service under it


def status() -> dict[str, bool]:
    return {"supervisor": running(), **{n: procutil.alive(_pid(n)) for n in specs()}}


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "status"
    if cmd == "run":
        return run()
    if cmd == "install":
        install()
        print("Tabbie starts at sign-in, and is starting now.")
    elif cmd == "uninstall":
        uninstall()
        print("Tabbie's background services are stopped and no longer start at sign-in.")
    elif cmd == "restart":
        names = [a for a in argv[1:] if not a.startswith("-")] or list(NAMES)
        request_restart(names)
        if not running():
            start()
        print("Restarting: " + ", ".join(names))
    else:
        for n, up in status().items():
            print(f"  {'running' if up else 'stopped'}  {n}")
    return 0
