"""Process control for macOS, Linux and Windows: start a worker in its own group, stop that whole
tree, check a pid, read its command line, find processes, and see who owns a local TCP port.

macOS / Linux: sessions + killpg, ps, pgrep, lsof (as before).
Windows: CREATE_NEW_PROCESS_GROUP, Ctrl+Break then `taskkill /T /F`, the Win32 API and PowerShell
CIM queries, `netstat -ano`.
"""
from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys

WINDOWS = sys.platform == "win32"

# Popen(**GROUP): the child leads its own process group, so stopping it stops everything it started
GROUP: dict = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if WINDOWS  # type: ignore[attr-defined]
               else {"start_new_session": True})
# a detached helper that outlives its parent (restarts, the Dreamer)
DETACHED: dict = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000008}  # type: ignore[attr-defined]
                  if WINDOWS else {"start_new_session": True})

# Exit codes of a worker we stopped: killed by SIGTERM/SIGKILL (negative, or 128+n through a shell);
# on Windows taskkill /F leaves 1 and Ctrl+Break 0xC000013A.
STOP_CODES = ((1, 0xC000013A, -1073741510) if WINDOWS
              else (-signal.SIGTERM, -signal.SIGKILL, 143, 137))


def _run(cmd: list[str], timeout: int = 10) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def stop(pid: int, force: bool = False) -> bool:
    """Stop pid's whole process group (gracefully, or force). False when it was already gone."""
    if not isinstance(pid, int) or pid <= 1:
        return False
    if WINDOWS:
        if not force:
            try:
                os.kill(pid, signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]  # the group's Ctrl+C
                return True
            except OSError:
                pass  # not a console group we started: fall through to taskkill
        try:
            r = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            return False
        return r.returncode == 0
    try:
        os.killpg(pid, signal.SIGKILL if force else signal.SIGTERM)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def alive(pid: int) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if WINDOWS:  # os.kill(pid, 0) would terminate the process on Windows
        import ctypes
        k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        try:
            return bool(k32.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def _cim(filter_: str) -> list[tuple[int, str]]:
    ps = shutil.which("powershell") or "powershell"
    out = _run([ps, "-NoProfile", "-NonInteractive", "-Command",
                f"Get-CimInstance Win32_Process {filter_} | ForEach-Object {{ \"$($_.ProcessId)`t$($_.CommandLine)\" }}"],
               timeout=20)
    rows = []
    for ln in out.splitlines():
        pid, _, cmd = ln.partition("\t")
        if pid.strip().isdigit():
            rows.append((int(pid), cmd.strip()))
    return rows


def cmdline(pid: int) -> str:
    """The process's command line, or '' when it isn't running."""
    if not alive(pid):
        return ""
    if WINDOWS:
        rows = _cim(f"-Filter \"ProcessId={int(pid)}\"")
        return rows[0][1] if rows else ""
    return _run(["ps", "-o", "command=", "-p", str(pid)], timeout=5).strip().split("\n")[0]


def find(pattern: str) -> list[int]:
    """Pids whose command line matches the regex (pgrep -f)."""
    if WINDOWS:
        rx = re.compile(pattern)
        return [pid for pid, cmd in _cim("") if pid != os.getpid() and rx.search(cmd)]
    return [int(x) for x in _run(["pgrep", "-f", pattern], timeout=10).split() if x.isdigit()]


def tcp_pids(port: int, listening: bool) -> set[int]:
    """Pids with a TCP socket on this local port: LISTEN, or an ESTABLISHED connection from it."""
    if WINDOWS:
        out = _run(["netstat", "-ano", "-p", "TCP"], timeout=10)
        want = "LISTENING" if listening else "ESTABLISHED"
        got = set()
        for ln in out.splitlines():
            parts = ln.split()
            if len(parts) >= 5 and parts[0] == "TCP" and parts[3] == want and parts[1].rsplit(":", 1)[-1] == str(port):
                if parts[4].isdigit():
                    got.add(int(parts[4]))
        return got
    lsof = shutil.which("lsof") or "/usr/sbin/lsof"
    out = _run([lsof, "-nP", f"-iTCP:{int(port)}", "-sTCP:" + ("LISTEN" if listening else "ESTABLISHED"), "-Fp"],
               timeout=5)
    return {int(x) for x in re.findall(r"^p(\d+)$", out, re.M)}


def argv(cmd: list[str]) -> list[str]:
    """cmd with its program resolved for this OS. On Windows `claude` / `codex` are often npm `.cmd`
    shims: batch files re-parse their arguments (& | ^ % in a prompt would break or run), so a shim is
    replaced by the `node <script.js>` it wraps; a native .exe is used as is."""
    if not WINDOWS or not cmd:
        return cmd
    path = shutil.which(cmd[0])
    if not path:
        return cmd
    if path.lower().endswith((".cmd", ".bat")):
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            text = ""
        m = re.search(r'"%(?:~dp0|dp0)%\\?([^"]+\.(?:js|mjs|cjs))"', text, re.I)
        node = shutil.which("node")
        if m and node:
            script = os.path.join(os.path.dirname(path), m.group(1).replace("\\", os.sep))
            if os.path.exists(script):
                return [node, script, *cmd[1:]]
        raise RuntimeError(f"{os.path.basename(path)} is a batch shim Tabbie can't run safely; "
                           f"install the native {cmd[0]}.exe")
    return [path, *cmd[1:]]
