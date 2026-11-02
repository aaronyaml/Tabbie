"""Windows setup (setup/windows.py) and hub (bin/hub.py): they load, and the safe steps run."""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
win = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")


def test_scripts_compile():
    for f in ("setup/windows.py", "bin/hub.py", "bin/tabbie-svc.pyw", "bin/tabbie-mdns.py"):
        compile((ROOT / f).read_text(encoding="utf-8"), f, "exec")


@win
def test_setup_name_step_writes_local_js(tmp_path):
    local = ROOT / "webui-extension" / "local.js"
    before = local.read_bytes() if local.exists() else None
    try:
        r = subprocess.run([sys.executable, str(ROOT / "setup" / "windows.py"), "--yes", "--only", "name",
                            "--name", "Sam"], capture_output=True, text=True, encoding="utf-8", timeout=120)
        assert r.returncode == 0, r.stdout + r.stderr
        text = local.read_text(encoding="utf-8")
        assert '"Sam"' in text and "window.HX_LOCAL" in text
    finally:
        if before is None:
            local.unlink(missing_ok=True)
        else:
            local.write_bytes(before)


@win
def test_hub_logs_and_help():
    r = subprocess.run([sys.executable, str(ROOT / "bin" / "hub.py"), "logs", "supervisor"],
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    r = subprocess.run([sys.executable, str(ROOT / "bin" / "hub.py"), "nope"],
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 2 and "hub health" in r.stdout
