"""Things an older iPhone or Mac can't run, which would break a whole page or setup there."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_no_regex_lookbehind_in_the_webui_extension():
    # Safari before 16.4 (iOS 16.3 and older) can't parse (?<= / (?<! : the whole script fails to load.
    for f in sorted((ROOT / "webui-extension").glob("*.js")):
        text = f.read_text(encoding="utf-8")
        assert not re.search(r"\(\?<[=!]", text), f"{f.name} uses a regex lookbehind"


def test_setup_checks_the_macos_version():
    text = (ROOT / "bin" / "setup-terminal").read_text(encoding="utf-8")
    assert "sw_vers -productVersion" in text and "13.5" in text
