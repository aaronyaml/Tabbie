"""Workers have no time limit: neither the worker run nor the hub's wait for it is cut off."""
from __future__ import annotations
import importlib.util, sys, types
from importlib import import_module
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "plugin" / "command-centre"
if "command_centre" not in sys.modules:
    spec = importlib.util.spec_from_file_location("command_centre", PKG / "__init__.py",
                                                  submodule_search_locations=[str(PKG)])
    _m = importlib.util.module_from_spec(spec); sys.modules["command_centre"] = _m
    spec.loader.exec_module(_m)
cc = sys.modules["command_centre"]
pv = import_module("command_centre.providers")


def test_run_limit_zero_or_unset_means_none():
    assert pv.run_limit({}) is None
    assert pv.run_limit({"timeout_seconds": 0}) is None
    assert pv.run_limit({"timeout_seconds": 600}) == 600


def test_run_stream_without_limit_finishes(tmp_path):
    code, out, err = pv._run_stream([sys.executable, "-c", "print('ok')"], cwd=str(tmp_path), env=None,
                                    timeout=None, stdin="", on_line=lambda _l: None)
    assert code == 0 and out.strip() == "ok" and "TIMEOUT" not in err


def test_worker_tools_lift_hub_deadline(monkeypatch):
    entry = types.SimpleNamespace(execution_timeout=None)
    fake = types.ModuleType("tools.registry")
    fake.registry = types.SimpleNamespace(get_entry=lambda n: entry if n == "cc_claude_execute" else None)
    monkeypatch.setitem(sys.modules, "tools", types.ModuleType("tools"))
    monkeypatch.setitem(sys.modules, "tools.registry", fake)
    assert "cc_claude_execute" in cc._WAITS_FOR_WORKER
    cc._lift_hub_deadline("cc_claude_execute")
    assert entry.execution_timeout >= 365 * 86400
