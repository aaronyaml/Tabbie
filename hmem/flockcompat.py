"""fcntl.flock for macOS, Linux and Windows. Same names, same behaviour: LOCK_EX blocks, LOCK_EX |
LOCK_NB raises BlockingIOError when another process holds the lock, LOCK_UN releases it.

    from hmem import flockcompat as fcntl   # drop-in for `import fcntl` + fcntl.flock(...)

Windows has no flock: msvcrt.locking on the file's first byte gives the same whole-file advisory
lock between processes (the byte may lie past the end of an empty lock file).
"""
from __future__ import annotations

import sys
import time

if sys.platform != "win32":
    from fcntl import LOCK_EX, LOCK_NB, LOCK_SH, LOCK_UN, flock  # noqa: F401
else:
    import msvcrt
    import os

    LOCK_SH, LOCK_EX, LOCK_NB, LOCK_UN = 1, 2, 4, 8

    def flock(f, op: int) -> None:
        fd = f if isinstance(f, int) else f.fileno()
        pos = os.lseek(fd, 0, os.SEEK_CUR)
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            if op & LOCK_UN:
                try:
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass  # not locked: flock(LOCK_UN) doesn't fail either
                return
            while True:
                try:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)  # shared locks are exclusive here
                    return
                except OSError:
                    if op & LOCK_NB:
                        raise BlockingIOError("lock is held by another process") from None
                    time.sleep(0.05)
        finally:
            os.lseek(fd, pos, os.SEEK_SET)
