"""Put the date and time in front of every line a background service prints (its log is a plain file that
launchd appends to). Without it, a support log can't say when anything happened. Stdlib only; used by the
Team service (tt daemon) and Tabbie's helper (sidecar)."""
from __future__ import annotations

import io
import sys
import time


class _Stamped(io.TextIOBase):
    def __init__(self, raw):
        self.raw = raw
        self.at_start = True

    def write(self, s: str) -> int:
        if not s:
            return 0
        out = []
        for part in s.splitlines(keepends=True):
            if self.at_start:
                out.append(time.strftime("%Y-%m-%d %H:%M:%S "))
            out.append(part)
            self.at_start = part.endswith("\n")
        self.raw.write("".join(out))
        return len(s)

    def flush(self):
        self.raw.flush()

    def isatty(self):
        return False

    def fileno(self):
        return self.raw.fileno()

    @property
    def encoding(self):
        return getattr(self.raw, "encoding", "utf-8")


def install() -> None:
    """Stamp stdout and stderr, unless they're a terminal (someone watching doesn't need it) or already stamped."""
    for name in ("stdout", "stderr"):
        f = getattr(sys, name)
        if f is None or isinstance(f, _Stamped) or f.isatty():
            continue
        setattr(sys, name, _Stamped(f))
