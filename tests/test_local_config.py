"""router.local.yaml overrides the shared config on one Mac; max_parallel fits the Mac's memory."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugin"))
def test_fit_parallel_by_memory():
    from test_updates import up  # noqa: F401  (loads the plugin package)
    import sys as _s
    c = next(m for n, m in _s.modules.items() if n.endswith(".common") and hasattr(m, "fit_parallel"))
    assert c.fit_parallel(10, {"gb": 8, "cores": 8}) == 4
    assert c.fit_parallel(10, {"gb": 16, "cores": 8}) == 8
    assert c.fit_parallel(10, {"gb": 64, "cores": 12}) == 10
    assert c.fit_parallel(10, {"gb": 4, "cores": 4}) == 2
    assert c.fit_parallel(1, {"gb": 4, "cores": 4}) == 1   # a deliberately low setting is kept


def test_local_file_overrides_and_pins_max_parallel(tmp_path, monkeypatch):
    from test_updates import up  # noqa: F401
    import sys as _s
    c = next(m for n, m in _s.modules.items() if n.endswith(".common") and hasattr(m, "fit_parallel"))
    (tmp_path / "router.yaml").write_text("executor:\n  max_parallel: 10\n  model: a\nother: 1\n")
    monkeypatch.setattr(c, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(c, "_HW", {"gb": 8, "cores": 8})
    assert c.load_config()["executor"] == {"max_parallel": 4, "model": "a"}
    (tmp_path / "router.local.yaml").write_text("executor:\n  max_parallel: 6\nother: 2\n")
    cfg = c.load_config()
    assert cfg["executor"] == {"max_parallel": 6, "model": "a"} and cfg["other"] == 2
