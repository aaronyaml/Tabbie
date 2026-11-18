"""Every library a launcher installs through uv must install as a ready-made wheel on Intel and Apple Silicon Macs.
cryptography 49 and later publish no Intel Mac wheels, so an Intel Mac would try (and fail) to compile it, and
an unpinned pywebpush pulls the newest cryptography. Pin both, everywhere."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAUNCHERS = [ROOT / "bin" / "tt", ROOT / "bin" / "cc-sidecar", ROOT / "hmem" / "winsvc.py"]


REQS = sorted((ROOT / "requirements").glob("*.txt"))


def test_cryptography_pinned_below_49():
    assert REQS
    for f in LAUNCHERS + REQS:
        for v in re.findall(r"cryptography==([\d.]+)", f.read_text()):
            assert int(v.split(".")[0]) < 49, f"{f.name} pins cryptography {v}: no Intel Mac wheels"


def test_every_package_is_locked_with_hashes():
    for f in REQS:
        s = f.read_text()
        pkgs = re.findall(r"^([a-z0-9][a-z0-9._-]*)(==|\s)", s, re.M)
        assert pkgs and all(op == "==" for _, op in pkgs), f"{f.name}: every package needs an exact version"
        assert s.count("--hash=sha256:") >= len(pkgs), f"{f.name}: hashes missing"
        assert (f.with_suffix(".in")).exists(), f"{f.name} has no .in source"


def test_launchers_install_from_the_lockfiles():
    for f in (ROOT / "bin" / "tt", ROOT / "bin" / "cc-sidecar", ROOT / "hmem" / "winsvc.py"):
        s = f.read_text()
        assert "--with-requirements" in s and not re.search(r"--with\b(?!-)", s.replace('"--with", "', '--with ')), f.name


def test_locked_packages_install_on_intel_and_apple_silicon():
    """Slow and needs the internet: CI sets TABBIE_LOCK_CHECK=1 (and bin/lock-deps runs it when re-locking)."""
    import os, shutil, subprocess
    import pytest
    if not os.environ.get("TABBIE_LOCK_CHECK") or not shutil.which("uv") or os.name == "nt":
        pytest.skip("set TABBIE_LOCK_CHECK=1")
    r = subprocess.run([str(ROOT / "bin" / "lock-deps"), "check"], capture_output=True, text=True, timeout=900)
    assert r.returncode == 0, r.stdout + r.stderr


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
