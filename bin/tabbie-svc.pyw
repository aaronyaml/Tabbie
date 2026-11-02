"""Tabbie's background services on Windows (see hmem/winsvc.py). .pyw: runs without a console window."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hmem import winsvc  # noqa: E402

sys.exit(winsvc.main(sys.argv[1:]))
