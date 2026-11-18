"""Each Mac's own name on the home network, and Caddy reading it (bin/tabbie-host, services/)."""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from test_updates import up

ROOT = Path(__file__).resolve().parents[1]


def test_caddyfile_takes_the_name_and_admin_port_from_the_service_and_quotes_paths():
    text = (ROOT / "services" / "Caddyfile").read_text(encoding="utf-8")
    assert "admin {$TABBIE_CADDY_ADMIN:localhost:2019}" in text
    assert re.search(r"^https://\{\$TABBIE_HOST:hermes\.local\} \{", text, re.M)
    assert not re.search(r"^https?://hermes\.local", text, re.M)
    assert not re.search(r'[^"]\{\$HOME\}', text), "a home folder with a space would split an unquoted path"
    plist = (ROOT / "services" / "ai.hermes.caddy.plist.template").read_text(encoding="utf-8")
    assert "__HOST__" in plist and "__CADDYADMIN__" in plist


@pytest.mark.skipif(not shutil.which("caddy"), reason="caddy not installed")
def test_caddy_uses_the_chosen_name(tmp_path):
    env = {**os.environ, "TABBIE_HOST": "hermes-leo.local", "TABBIE_CADDY_ADMIN": "localhost:2029", "HOME": str(tmp_path / "a b")}
    out = subprocess.run(["caddy", "adapt", "--config", str(ROOT / "services" / "Caddyfile"), "--adapter", "caddyfile"],
                         capture_output=True, text=True, env=env, timeout=60).stdout
    assert "hermes-leo.local" in out and "localhost:2029" in out and "a b/hermes/brand" in out
    assert '"hermes.local"' not in out


def test_updater_fills_the_saved_name_into_service_files(tmp_path, monkeypatch):
    monkeypatch.setattr(up, "ROOT", tmp_path)
    t = tmp_path / "x.plist.template"
    t.write_text("<s>__HOME__ __HOST__ __CADDYADMIN__</s>")
    assert up._render(t) == f"<s>{Path.home()} hermes.local localhost:2019</s>"  # nothing chosen: as before
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "local-name").write_text("hermes-leo\n")
    (tmp_path / "state" / "caddy-admin").write_text("localhost:2029\n")
    assert up._render(t) == f"<s>{Path.home()} hermes-leo.local localhost:2029</s>"


def test_tabbie_host_reads_the_saved_name(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    shutil.copy(ROOT / "bin" / "tabbie-host", bin_ / "tabbie-host")
    run = lambda *a: subprocess.run([str(bin_ / "tabbie-host"), *a], capture_output=True, text=True, timeout=30).stdout.strip()  # noqa: E731
    assert run() == "hermes.local"
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "local-name").write_text("hermes-sam\n")
    assert run() == "hermes-sam.local" and run("--choose") == "hermes-sam"


def test_mdns_publishes_every_lan_address_not_just_the_default_route():
    text = (ROOT / "bin" / "hermes-mdns").read_text(encoding="utf-8")
    assert "route -n get default" not in text and "ifconfig -l" in text and "sleep 10" in text
