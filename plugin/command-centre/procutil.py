"""Cross-platform process control (see hmem/procutil.py): `from .procutil import procutil`."""
from __future__ import annotations

import sys
from pathlib import Path

_home = str(Path(__file__).resolve().parents[2])
if _home not in sys.path:
    sys.path.insert(0, _home)
from hmem import procutil  # noqa: E402,F401
