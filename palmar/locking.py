"""One exclusive lock per home, on either platform -- the same shape as posixpty/conpty.

`fcntl` is the **only** import in daemon.py that does not exist on Windows at all, so it is what
kept the daemon from even being imported there (#29). The seven call sites were all one pattern:
take it non-blocking, and treat OSError as "somebody else has it, which is the answer I wanted".

**Both kinds release when the holder dies.** That is the property the lock is actually used for --
a daemon killed with SIGKILL must not leave a home locked forever. POSIX `flock` has always done
this; that `msvcrt.locking` does too was measured on a runner (docs/windows.md, "jaen geot").

**Not the same lock, and that is fine.** flock is advisory on the whole file; msvcrt.locking is a
mandatory byte-range lock on the first byte. Nothing else opens this file for anything but these
calls and a 256-byte read of the pid line, so the difference never shows.
"""
from __future__ import annotations

import os
import sys

if sys.platform == "win32":
    import msvcrt

    def take(fd: int) -> None:
        """Take it, or raise OSError because somebody else holds it.

        The lock is on **byte 0**, so the position has to be there and then put back -- the caller
        reads and writes the pid line through the same descriptor."""
        at = os.lseek(fd, 0, os.SEEK_CUR)
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        finally:
            os.lseek(fd, at, os.SEEK_SET)

    def release(fd: int) -> None:
        at = os.lseek(fd, 0, os.SEEK_CUR)
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass                    # already gone; releasing twice is not an error worth raising
        finally:
            os.lseek(fd, at, os.SEEK_SET)

else:
    import fcntl

    def take(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def release(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


#: `O_NOFOLLOW` refuses to open a symlink, which is why the lock is opened with it -- somebody who
#: can drop a link in `~/.palmar/run/` should not get us to lock a file of their choosing. Windows
#: has no such flag; the directory is already 0700-equivalent by profile ACL there (docs/windows.md).
NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
