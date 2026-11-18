"""Updates install what the new version needs before switching to it, check the services come back, and go
back to the previous version if they don't. Launchers back off when a service keeps failing to start."""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from test_updates import up  # same module object the other update tests use

ROOT = Path(__file__).resolve().parents[1]


def _git(*a, cwd):
    return subprocess.run(["git", "-C", str(cwd), *a], check=True, capture_output=True, text=True).stdout.strip()


def _repos(tmp_path, monkeypatch, new_files: dict):
    remote, local = tmp_path / "remote", tmp_path / "local"
    remote.mkdir()
    _git("init", "-q", "-b", "main", cwd=remote)
    _git("config", "user.email", "t@t", cwd=remote); _git("config", "user.name", "t", cwd=remote)
    (remote / "ok.py").write_text("x = 1\n")
    (remote / ".gitignore").write_text("state/\n")
    _git("add", ".", cwd=remote); _git("commit", "-qm", "one", cwd=remote)
    subprocess.run(["git", "clone", "-q", str(remote), str(local)], check=True)
    for name, text in new_files.items():
        p = remote / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        if name.startswith("bin/"):
            os.chmod(p, 0o755)
    _git("add", ".", cwd=remote); _git("commit", "-qm", "two", cwd=remote)
    monkeypatch.setattr(up, "ROOT", local)
    monkeypatch.setattr(up, "STATE", local / "state" / "updates.json")
    monkeypatch.setattr(up, "_url", lambda s: str(remote))
    monkeypatch.setattr(up, "settings", lambda: {"branch": "main", "repo": "x/y"})
    monkeypatch.setattr(up, "check", lambda force=False: {"available": True})
    monkeypatch.setattr(up, "ensure_services", lambda: [])
    monkeypatch.setattr(up, "_team_installed", lambda: False)
    monkeypatch.delenv("TABBIE_SKIP_PREFLIGHT", raising=False)
    return remote, local


def test_new_code_that_doesnt_parse_is_never_switched_to(tmp_path, monkeypatch):
    _, local = _repos(tmp_path, monkeypatch, {"team/broken.py": "def f(:\n"})
    before = _git("rev-parse", "HEAD", cwd=local)
    res = up.apply()
    assert not res["ok"] and "nothing changed" in res["error"] and "broken.py" in res["error"]
    assert _git("rev-parse", "HEAD", cwd=local) == before
    assert up.last_failure()["rolled_back"] is False


def test_a_launcher_that_cant_install_its_components_blocks_the_update(tmp_path, monkeypatch):
    if os.name == "nt":
        return
    _, local = _repos(tmp_path, monkeypatch, {"bin/cc-sidecar": "#!/bin/sh\n# --preflight\necho 'No solution found for cryptography' >&2\nexit 1\n"})
    before = _git("rev-parse", "HEAD", cwd=local)
    res = up.apply()
    assert not res["ok"] and "helper" in res["error"] and "No solution found" in res["error"]
    assert _git("rev-parse", "HEAD", cwd=local) == before


def test_preflight_passes_and_optional_parts_become_warnings(tmp_path, monkeypatch):
    if os.name == "nt":
        return
    _, local = _repos(tmp_path, monkeypatch, {"bin/cc-sidecar": "#!/bin/sh\n# --preflight\necho 'optional: push is off'\nexit 0\n"})
    res = up.apply()
    assert res["ok"], res
    assert res["warnings"] == ["push is off"]
    assert (local / "bin" / "cc-sidecar").exists()


def test_service_that_doesnt_come_back_rolls_back(tmp_path, monkeypatch):
    _, local = _repos(tmp_path, monkeypatch, {"plugin/command-centre/sidecar.py": "x = 2\n"})
    before = _git("rev-parse", "HEAD", cwd=local)
    assert up.apply()["ok"]
    after = _git("rev-parse", "HEAD", cwd=local)
    assert after != before
    monkeypatch.setattr(up, "_healthy", lambda name, since: False if name == "sidecar" else None)
    monkeypatch.setattr(up.time, "sleep", lambda s: None)
    restarts = []
    monkeypatch.setattr(up, "restart_detached_wait", lambda max_wait=3600: restarts.append(up.pending_restart()) or {"ok": True})
    res = up.verify(["sidecar"], timeout=0.1)
    assert not res["ok"] and res["rolled_back"]
    assert _git("rev-parse", "HEAD", cwd=local) == before
    f = up.last_failure()
    assert f["rolled_back"] and f["to"] == after and "helper" in f["step"]
    assert restarts and "sidecar" in restarts[0]  # the old version is restarted too
    # a second failure never ping-pongs back to the broken version
    assert up.verify(["sidecar"], timeout=0.1).get("rolled_back") is None


def test_healthy_services_clear_the_check(tmp_path, monkeypatch):
    _repos(tmp_path, monkeypatch, {"plugin/command-centre/sidecar.py": "x = 2\n"})
    assert up.apply()["ok"]
    monkeypatch.setattr(up, "_healthy", lambda name, since: True)
    monkeypatch.setattr(up.time, "sleep", lambda s: None)
    t = [1000.0]
    monkeypatch.setattr(up.time, "time", lambda: (t.__setitem__(0, t[0] + 5), t[0])[1])
    assert up.verify(["sidecar", "webui"])["ok"]
    assert up.last_failure() is None and up._state()["verified"]


def test_rollback_keeps_local_edits(tmp_path, monkeypatch):
    _, local = _repos(tmp_path, monkeypatch, {"plugin/command-centre/sidecar.py": "x = 2\n"})
    assert up.apply()["ok"]
    (local / "ok.py").write_text("x = 'mine'\n")
    monkeypatch.setattr(up, "restart_detached_wait", lambda max_wait=3600: {"ok": True})
    assert up.rollback("test")["ok"]
    assert (local / "ok.py").read_text(encoding="utf-8") == "x = 'mine'\n"
    assert not (local / "plugin" / "command-centre" / "sidecar.py").exists()


def test_backoff_waits_longer_each_fast_failure(tmp_path):
    if os.name == "nt":
        return
    home = tmp_path
    (home / "hermes" / "state" / "run").mkdir(parents=True)
    script = f'sleep() {{ echo "slept $1"; }}; . "{ROOT}/bin/tabbie-backoff"; tabbie_backoff svc'
    run = lambda: subprocess.run(["bash", "-c", script], env={**os.environ, "HOME": str(home)}, capture_output=True, text=True)
    outs = [run() for _ in range(4)]
    assert "slept" not in outs[0].stdout and "slept" not in outs[1].stdout  # one quick restart never waits
    assert "slept 20" in outs[2].stdout and "slept 40" in outs[3].stdout
    assert "trying again in 40s" in outs[3].stderr
    f = home / "hermes" / "state" / "run" / "svc.starts"
    f.write_text(f"{int(time.time()) - 600} 5\n")  # ran for 10 minutes: counts as healthy, starts over
    assert "slept" not in run().stdout


def test_launchers_use_backoff_and_have_preflight():
    for name in ("cc-sidecar", "tt", "hermes-webui-run"):
        assert "tabbie_backoff" in (ROOT / "bin" / name).read_text(encoding="utf-8"), name
    for name in ("cc-sidecar", "tt"):
        assert "--preflight" in (ROOT / "bin" / name).read_text(encoding="utf-8"), name


def test_updater_is_standard_library_only():
    import ast
    src = (ROOT / "plugin" / "command-centre" / "updates.py").read_text(encoding="utf-8")
    mods = {n.names[0].name.split(".")[0] for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Import)}
    mods |= {n.module.split(".")[0] for n in ast.walk(ast.parse(src)) if isinstance(n, ast.ImportFrom) and n.module and not n.level}
    import sys
    assert mods <= set(sys.stdlib_module_names) | {"hmem"}, mods - set(sys.stdlib_module_names)
    tpl = (ROOT / "services" / "ai.hermes.updater.plist.template").read_text(encoding="utf-8")
    assert "/usr/bin/python3" in tpl and "tabbie-update" in tpl and "KeepAlive" not in tpl
