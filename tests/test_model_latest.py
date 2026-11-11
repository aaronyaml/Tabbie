"""Each model family resolves to its newest version: Fable 5.1 -> 5.2 once 5.2 is known."""
from __future__ import annotations
import importlib.util, json, sys
from importlib import import_module
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "plugin" / "command-centre"
if "command_centre" not in sys.modules:
    spec = importlib.util.spec_from_file_location("command_centre", PKG / "__init__.py",
                                                  submodule_search_locations=[str(PKG)])
    _m = importlib.util.module_from_spec(spec); sys.modules["command_centre"] = _m
    spec.loader.exec_module(_m)
ml = import_module("command_centre.model_latest")
ec = import_module("command_centre.executor_choice")


def _setup(tmp_path, monkeypatch, anthropic=None, codex=None):
    monkeypatch.setattr(ml, "CACHE", tmp_path / "ml.json")
    monkeypatch.setattr(ml, "CODEX_CACHE", tmp_path / "codex.json")
    ml._mem.update(data=None, mtime=0.0)
    (tmp_path / "ml.json").write_text(json.dumps({"checked_at": 9e12, "anthropic": anthropic or {}}))
    if codex is not None:
        (tmp_path / "codex.json").write_text(json.dumps({"models": codex}))


def test_parse_and_names():
    assert ml.parse("claude-fable-5-2") == ("anthropic", "fable", (5, 2))
    assert ml.parse("gpt-6.1-sol") == ("openai", "sol", (6, 1))
    assert ml.parse("gpt-6-astra") == ("openai", "astra", (6,))
    assert ml.parse("deepseek-v4") is None
    assert ml.display_name("claude-fable-5-2") == "Fable 5.2"
    assert ml.display_name("gpt-6.1-terra") == "GPT-6.1 Terra"


def test_upgrade_never_downgrades(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, anthropic={"fable": {"id": "claude-fable-5-2", "name": "Fable 5.2"}})
    assert ml.upgrade("claude-fable-5-1") == "claude-fable-5-2"
    assert ml.upgrade("claude-fable-5-3") == "claude-fable-5-3"
    assert ml.upgrade("claude-opus-5-5") == "claude-opus-5-5"  # family not resolved yet: unchanged
    assert ml.upgrade("kimi-k2") == "kimi-k2"


def test_codex_newest_listed_per_family(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, codex=[
        {"slug": "gpt-6.1-sol", "visibility": "list"}, {"slug": "gpt-6-sol", "visibility": "list"},
        {"slug": "gpt-6.2-astra", "visibility": "hide"}, {"slug": "gpt-6-astra", "visibility": "list"},
        {"slug": "gpt-7-luna", "visibility": "list"}, {"slug": "codex-auto-review", "visibility": "hide"}])
    f = ml.families()["openai"]
    assert f["sol"]["id"] == "gpt-6.1-sol" and f["astra"]["id"] == "gpt-6-astra" and f["luna"]["id"] == "gpt-7-luna"


def test_catalog_and_choices_show_newest(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, anthropic={"fable": {"id": "claude-fable-5-2", "name": "Fable 5.2"}})
    names = [m["name"] for m in ec._models("anthropic")]
    assert "Fable 5.2" in names and "Fable 5.1" not in names
    assert ec.model_info("anthropic", "claude-fable-5-1")["id"] == "claude-fable-5-2"
    assert ec.claude_args("claude-fable-5-1", None) == ["--model", "claude-fable-5-2"]
    assert ec.peer_model("openai", "gpt-6-astra")["model"] == "claude-fable-5-2"
