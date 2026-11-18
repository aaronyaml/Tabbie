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


# Which Python runs Tabbie must not depend on what the Mac has installed (Apple's 3.9, a stray 3.11...).
UV_LAUNCHERS = LAUNCHERS + [ROOT / "bin" / f for f in ("hub", "hub.py", "hub.cmd", "hmem.cmd")]
PY_RANGE = ">=3.12,<3.15"


def test_every_uv_run_names_a_python():
    for f in UV_LAUNCHERS:
        s = f.read_text()
        arrays = dict(re.findall(r"^\s*(\w+)=\((.*)\)\s*(?:#.*)?$", s, re.M))
        for line in s.splitlines():
            if "--no-project" not in line or line.lstrip().startswith(("#", "rem ")):
                continue
            used = re.findall(r"\$\{(\w+)\[@\]\}", line)
            ok = "--python" in line or any("--python" in arrays.get(a, "") for a in used)
            assert ok, f"{f.name}: uv run without --python: {line.strip()[:100]}"
    assert PY_RANGE in (ROOT / "bin" / "tt").read_text() and PY_RANGE in (ROOT / "hmem" / "winsvc.py").read_text()


def test_code_parses_on_oldest_supported_python():
    import shutil, subprocess
    uv = shutil.which("uv")
    if not uv:
        import pytest
        pytest.skip("uv not installed")
    code = ("import ast,pathlib,sys\nbad=[]\nfor p in pathlib.Path(sys.argv[1]).rglob('*.py'):\n"
            " if any(x in p.parts for x in ('hermes-webui','node_modules','.git','state','memory','.venv')): continue\n"
            " try: ast.parse(p.read_text(encoding='utf-8'),str(p))\n except SyntaxError as e: bad.append(f'{p}:{e.lineno}')\n"
            "print('\\n'.join(bad)); sys.exit(1 if bad else 0)")
    r = subprocess.run([uv, "run", "-q", "--no-project", "--python", "3.12", "python", "-I", "-c", code, str(ROOT)],
                       capture_output=True, text=True, timeout=300)
    if r.returncode and "No interpreter found" in r.stderr:
        import pytest
        pytest.skip("Python 3.12 not available offline")
    assert r.returncode == 0, f"files that don't parse on Python 3.12: {r.stdout}{r.stderr[-400:]}"
