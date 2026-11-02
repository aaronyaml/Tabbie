"""Cross-platform process helpers (hmem/procutil.py)."""
from __future__ import annotations
import subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hmem import procutil  # noqa: E402

SLEEPER = [sys.executable, "-c", "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); time.sleep(60)"]


def test_stop_ends_the_whole_group():
    p = subprocess.Popen(SLEEPER, **procutil.GROUP)
    time.sleep(1.0)
    assert procutil.alive(p.pid)
    assert procutil.stop(p.pid, force=True)
    p.wait(timeout=15)
    assert not procutil.alive(p.pid) or p.poll() is not None
    assert not procutil.stop(-5)


def test_cmdline_and_find():
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)  # tabbie-procutil-probe"], **procutil.GROUP)
    try:
        time.sleep(0.8)
        assert "tabbie-procutil-probe" in procutil.cmdline(p.pid)
        assert p.pid in procutil.find("tabbie-procutil-probe")
    finally:
        procutil.stop(p.pid, force=True)
        p.wait(timeout=15)
    assert procutil.cmdline(p.pid) == ""


def test_argv_is_unchanged_off_windows():
    if procutil.WINDOWS:
        return
    assert procutil.argv(["claude", "-p"]) == ["claude", "-p"]


def test_tcp_pids_finds_a_listener():
    import socket
    s = socket.socket(); s.bind(("127.0.0.1", 0)); s.listen(1)
    try:
        assert __import__("os").getpid() in procutil.tcp_pids(s.getsockname()[1], listening=True)
    finally:
        s.close()


def test_windows_style_credential_paths_are_caught():
    from hmem import security
    assert security.is_credential_path(r"C:\Users\me\.ssh\id_rsa")
    assert security.is_credential_path(r"C:\Users\me\.codex\auth.json")
    assert security.is_credential_path(r"C:\Users\me\.claude\.credentials.json")
    assert not security.is_credential_path(r"C:\Users\me\code\app\main.py")


def test_attachments_secret_check_and_bare_windows_paths():
    import importlib.util
    pkg = Path(__file__).resolve().parents[1] / "plugin" / "command-centre"
    if "command_centre" not in sys.modules:
        spec = importlib.util.spec_from_file_location("command_centre", pkg / "__init__.py", submodule_search_locations=[str(pkg)])
        m = importlib.util.module_from_spec(spec); sys.modules["command_centre"] = m; spec.loader.exec_module(m)
    att = __import__("importlib").import_module("command_centre.attachments")
    assert att._is_secret(r"C:\Users\me\.aws\credentials")
    assert att._is_secret(r"C:\Users\me\.ssh\id_ed25519")
    found = [m.group(1) for m in att._BARE_PATH.finditer(r"look at C:\Users\me\Pictures\shot.png please")]
    assert found == [r"C:\Users\me\Pictures\shot.png"]
