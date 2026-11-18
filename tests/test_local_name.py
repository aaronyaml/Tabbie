"""Tabbie is always https://hermes.local; Caddy's admin port can move; paths with spaces work."""
import os
import re
import shutil
import sys
import subprocess
from pathlib import Path

import pytest

from test_updates import up

ROOT = Path(__file__).resolve().parents[1]


def test_caddyfile_serves_hermes_local_and_quotes_paths():
    text = (ROOT / "services" / "Caddyfile").read_text(encoding="utf-8")
    assert "admin {$TABBIE_CADDY_ADMIN:localhost:2019}" in text
    assert re.search(r"^https://hermes\.local \{", text, re.M) and re.search(r"^http://hermes\.local \{", text, re.M)
    assert not re.search(r'[^"]\{\$HOME\}', text), "a home folder with a space would split an unquoted path"


@pytest.mark.skipif(not shutil.which("caddy"), reason="caddy not installed")
def test_caddy_admin_port_and_spaced_home(tmp_path):
    env = {**os.environ, "TABBIE_CADDY_ADMIN": "localhost:2029", "HOME": str(tmp_path / "a b")}
    out = subprocess.run(["caddy", "adapt", "--config", str(ROOT / "services" / "Caddyfile"), "--adapter", "caddyfile"],
                         capture_output=True, text=True, env=env, timeout=60).stdout
    assert "hermes.local" in out and "localhost:2029" in out and "a b/hermes/brand" in out


def test_updater_fills_the_saved_admin_port(tmp_path, monkeypatch):
    monkeypatch.setattr(up, "ROOT", tmp_path)
    t = tmp_path / "x.plist.template"
    t.write_text("<s>__HOME__ __CADDYADMIN__</s>")
    assert up._render(t) == f"<s>{Path.home()} localhost:2019</s>"
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "caddy-admin").write_text("localhost:2029\n")
    assert up._render(t) == f"<s>{Path.home()} localhost:2029</s>"


@pytest.mark.skipif(sys.platform == "win32", reason="bash script")
def test_address_is_always_hermes_local():
    out = subprocess.run([str(ROOT / "bin" / "tabbie-host")], capture_output=True, text=True, timeout=30).stdout.strip()
    assert out == "hermes.local"


def test_mdns_publishes_every_lan_address_not_just_the_default_route():
    text = (ROOT / "bin" / "hermes-mdns").read_text(encoding="utf-8")
    assert "route -n get default" not in text and "ifconfig -l" in text and "sleep 10" in text
    assert 'NAME="${HERMES_MDNS_NAME:-hermes.local}"' in text
