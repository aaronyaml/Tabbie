"""Support logs: secrets blanked, big logs rotated, log lines stamped with the time."""
import importlib.machinery
import importlib.util
import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _support():
    loader = importlib.machinery.SourceFileLoader("tabbie_support_t", str(ROOT / "bin" / "tabbie-support"))
    spec = importlib.util.spec_from_loader("tabbie_support_t", loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


def test_secrets_are_blanked_but_commit_ids_stay():
    s = _support().scrub
    assert "hunter22" not in s("password=hunter22 next")
    assert "abc.def" not in s("Authorization: Bearer abc.def.ghi123")
    assert "sk-ant" not in s("key sk-ant-oat01-abcdefghijklmnop")
    assert "[email]" in s("from someone@example.com")
    assert "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-AbCd" not in s("t AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-AbCd")
    sha = "6d1a22b3c4d5e6f708192a3b4c5d6e7f80912345"
    assert sha in s(f"from {sha} to")
    assert str(Path.home()) not in s(f"{Path.home()}/hermes/x")


def test_big_logs_are_rotated_in_place(tmp_path):
    from test_updates import up
    big, small = tmp_path / "svc.log", tmp_path / "quiet.log"
    big.write_bytes(b"x\n" * 600)
    small.write_bytes(b"y\n")
    fh = open(big, "ab")  # the service keeps its file open and appends
    assert up.rotate_logs([tmp_path], limit=1000) == ["svc.log"]
    fh.write(b"after\n")
    fh.close()
    assert big.read_bytes() == b"after\n" and (tmp_path / "svc.log.1").stat().st_size == 1200
    assert small.read_bytes() == b"y\n" and not (tmp_path / "quiet.log.1").exists()


def test_service_output_is_stamped_with_the_time():
    sys.path.insert(0, str(ROOT / "team"))
    import logstamp
    buf = io.StringIO()
    w = logstamp._Stamped(buf)
    w.write("one\ntw")
    w.write("o\nthree\n")
    lines = buf.getvalue().splitlines()
    assert [re.sub(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d ", "", x) for x in lines] == ["one", "two", "three"]
    assert all(re.match(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d ", x) for x in lines)
