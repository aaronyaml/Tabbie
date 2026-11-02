"""Cross-platform flock (see hmem/flockcompat.py): `from .flockcompat import fcntl`."""
from __future__ import annotations

import sys
from pathlib import Path

_home = str(Path(__file__).resolve().parents[2])
if _home not in sys.path:
    sys.path.insert(0, _home)
from hmem import flockcompat as fcntl  # noqa: E402,F401
