"""Every library a launcher installs through uv must install as a ready-made wheel on Intel and Apple Silicon Macs.
cryptography 49 and later publish no Intel Mac wheels, so an Intel Mac would try (and fail) to compile it, and
an unpinned pywebpush pulls the newest cryptography. Pin both, everywhere."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAUNCHERS = [ROOT / "bin" / "tt", ROOT / "bin" / "cc-sidecar", ROOT / "hmem" / "winsvc.py"]


def test_cryptography_pinned_below_49():
    for f in LAUNCHERS:
        for v in re.findall(r"cryptography==([\d.]+)", f.read_text()):
            assert int(v.split(".")[0]) < 49, f"{f.name} pins cryptography {v}: no Intel Mac wheels"


def test_pywebpush_is_pinned_with_cryptography():
    for f in LAUNCHERS:
        s = f.read_text()
        if "pywebpush" in s:
            assert re.search(r"pywebpush==", s) and "cryptography==" in s, f"{f.name}: pin pywebpush and cryptography"


def test_sidecar_starts_without_push_libraries():
    s = (ROOT / "bin" / "cc-sidecar").read_text()
    assert s.count("exec \"$UV\" run") == 2, "cc-sidecar must fall back to starting without pywebpush"
