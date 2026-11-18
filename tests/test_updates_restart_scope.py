import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugin"))


def _updates():
    import types
    pkg = types.ModuleType("cc_pkg")
    pkg.__path__ = [str(Path(__file__).resolve().parents[1] / "plugin" / "command-centre")]
    sys.modules.setdefault("cc_pkg", pkg)
    return importlib.import_module("cc_pkg.updates")


def test_helper_only_change_restarts_only_the_helper():
    u = _updates()
    assert u._needs(["plugin/command-centre/team_link.py"]) == ["sidecar"]
    assert u._needs(["plugin/command-centre/sidecar.py", "webui-extension/team.js"]) == ["reload", "sidecar"]


def test_webui_code_still_restarts_everything():
    u = _updates()
    assert u._needs(["plugin/command-centre/router.py"]) == ["gateway", "sidecar", "webui"]
    assert u._needs(["hmem/paths.py"]) == ["gateway", "sidecar", "webui"]


def test_team_code_restarts_the_team_service():
    u = _updates()
    assert u._needs(["team/mailroom.py"]) == ["team"]
    assert u._needs(["bin/tt"]) == ["team"]


def test_launcher_changes_restart_what_they_launch():
    u = _updates()
    assert u._needs(["bin/cc-sidecar"]) == ["sidecar"]
    assert u._needs(["bin/hermes-webui-run"]) == ["webui"]


def test_locked_package_changes_restart_their_service():
    u = _updates()
    assert u._needs(["requirements/team.txt"]) == ["team"]
    assert u._needs(["requirements/sidecar-push.txt"]) == ["sidecar"]
