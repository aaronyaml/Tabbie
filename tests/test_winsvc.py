"""Windows background services (hmem/winsvc.py): restart requests, the nightly Dreamer, the supervisor."""
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hmem import procutil, winsvc  # noqa: E402


@pytest.fixture
def run_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(winsvc, "RUN", tmp_path / "run")
    monkeypatch.setattr(winsvc, "LOGS", tmp_path / "logs")
    (tmp_path / "run").mkdir()
    return tmp_path


def test_restart_requests_wait_until_due(run_dir):
    winsvc.request_restart(["webui"], delay=60)
    winsvc.request_restart(["sidecar"])
    assert winsvc._requests() == {"sidecar"}
    assert winsvc._requests() == set()  # taken once
    assert (run_dir / "run" / "restart.json").exists()  # the webui one is still waiting


def test_dreamer_runs_once_a_night_after_three_thirty(run_dir):
    assert not winsvc._dream_due(datetime(2026, 1, 1, 12, 0))  # first start: tomorrow night
    assert not winsvc._dream_due(datetime(2026, 1, 2, 3, 0))
    assert winsvc._dream_due(datetime(2026, 1, 2, 3, 31))
    assert not winsvc._dream_due(datetime(2026, 1, 2, 9, 0))
    assert winsvc._dream_due(datetime(2026, 1, 4, 8, 0))  # asleep at 03:30: the first wake after it


def test_specs_always_have_the_sidecar():
    s = winsvc.specs()
    assert "sidecar" in s and str(s["sidecar"]["argv"][-1]).endswith("sidecar.py")
    assert set(s) <= set(winsvc.NAMES)


@pytest.mark.skipif(sys.platform != "win32", reason="the supervisor runs on Windows")
def test_supervisor_restarts_a_service_and_stops(run_dir, monkeypatch):
    sleeper = {"argv": [sys.executable, "-c", "import time; time.sleep(600)"], "cwd": run_dir,
               "log": run_dir / "logs" / "s.log"}
    monkeypatch.setattr(winsvc, "specs", lambda: {"sidecar": dict(sleeper)})
    monkeypatch.setattr(winsvc, "_POLL", 0.2)
    monkeypatch.setattr(winsvc, "_dream_due", lambda now: False)
    t = threading.Thread(target=winsvc.run, daemon=True)
    t.start()

    def pid_after(old):
        for _ in range(100):
            p = winsvc._pid("sidecar")
            if p and p != old and procutil.alive(p):
                return p
            time.sleep(0.1)
        raise AssertionError("service didn't start")

    first = pid_after(0)
    assert winsvc.running()
    procutil.stop(first, force=True)  # it crashed: started again
    second = pid_after(first)
    winsvc.request_restart(["sidecar"])
    third = pid_after(second)
    winsvc.request_restart(["stop"])
    t.join(timeout=20)
    assert not t.is_alive() and not procutil.alive(third)
