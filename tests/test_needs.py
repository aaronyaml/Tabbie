"""needs.py: Tabbie asks for whatever it needs and doesn't have (first name today)."""
from __future__ import annotations
import importlib.util, sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1] / "plugin" / "command-centre"
spec = importlib.util.spec_from_file_location("cc_needs_test", PKG / "needs.py")
needs = importlib.util.module_from_spec(spec); spec.loader.exec_module(needs)


@pytest.fixture
def local(tmp_path, monkeypatch):
    f = tmp_path / "local.js"
    monkeypatch.setattr(needs, "LOCAL_JS", f)
    return f


def test_missing_name_is_asked(local):
    local.write_text('window.HX_LOCAL = {\n  home: "/h",\n  hermes: "/h/hermes",\n  motion: "/h/motion",\n  name: "",\n};\n')
    assert [n["key"] for n in needs.missing()] == ["name"]


def test_no_local_file_asks_too(local):
    assert [n["key"] for n in needs.missing()] == ["name"]


def test_save_keeps_other_fields(local):
    local.write_text('window.HX_LOCAL = {\n  home: "/h",\n  hermes: "/h/hermes",\n  motion: "/m/x",\n  name: "",\n};\n')
    needs.save("name", "  Sam  ")
    d = needs._local()
    assert d["name"] == "Sam" and d["motion"] == "/m/x" and d["hermes"] == "/h/hermes"
    assert needs.missing() == []


def test_single_quoted_example_is_read(local):
    local.write_text("window.HX_LOCAL = {\n  home: '/Users/you',          // your home folder\n  name: 'Ana',\n};\n")
    assert needs._local()["name"] == "Ana" and needs.missing() == []


def test_bad_values_refused(local):
    with pytest.raises(ValueError):
        needs.save("name", "   ")
    with pytest.raises(ValueError):
        needs.save("nope", "x")
