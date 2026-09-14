"""One exclusive lock per home, on either platform -- the same shape as posixpty/conpty.

`fcntl` is the **only** import in daemon.py that does not exist on Windows at all, so it is what
kept the daemon from even being imported there (#29). The seven call sites were all one pattern:
take it non-blocking, and treat OSError as "somebody else has it, which is the answer I wanted".

**Both kinds release when the holder dies.** That is the property the lock is actually used for --
a daemon killed with SIGKILL must not leave a home locked forever. POSIX `flock` has always done
this; that `msvcrt.locking` does too was measured on a runner (docs/windows.md, "jaen geot").

**Not the same lock, and the difference bites.** flock is advisory on the whole file; msvcrt.locking
is *mandatory* on a byte range. That is why the Windows side locks a byte far past any content: on
the first byte it also locked the pid line **against being read**, and `--stop`, which has to read
that pid, was refused by the very daemon it was trying to stop.
"""
from __future__ import annotations

import os
import sys

if sys.platform == "win32":
    import msvcrt

    #: **Far past anything the file will ever hold, and that is the point.** `msvcrt.locking` is a
    #: *mandatory* byte-range lock, where POSIX `flock` is advisory on the whole file. Locking byte 0
    #: -- where the pid line lives -- meant the holder's own file could not be read by anyone else, so
    #: `palmar --stop` came back "permission denied" while reading the pid it needed (user,
    #: 2026-09-14). Locking a byte nothing will ever occupy keeps the exclusion and gives the content
    #: back. A lock beyond end-of-file is allowed on Windows and still excludes a second holder --
    #: measured on a runner, alongside three other variants, before this was written the wrong way.
    LOCK_BYTE = 1 << 30

    def take(fd: int) -> None:
        """Take it, or raise OSError because somebody else holds it."""
        at = os.lseek(fd, 0, os.SEEK_CUR)
        os.lseek(fd, LOCK_BYTE, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        finally:
            os.lseek(fd, at, os.SEEK_SET)

    def release(fd: int) -> None:
        at = os.lseek(fd, 0, os.SEEK_CUR)
        os.lseek(fd, LOCK_BYTE, os.SEEK_SET)
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


#: **Windows opens in text mode unless told otherwise, and every file here is bytes.** In text mode
#: the CRT translates `\n` to `\r\n` on the way out and buffers on the way in -- so the pid line
#: written into run/lock does not have the length it was given, and a read after a seek may not start
#: where it was asked to. That is the best explanation for why reading *past* a locked first byte
#: still came back "permission denied" on a real machine (2026-09-14) -- **a hypothesis, not a
#: measurement**: by then the transition it mattered for was over and there was nothing left to
#: reproduce it against. Zero everywhere else, so it costs POSIX nothing.
BINARY = getattr(os, "O_BINARY", 0)


#: `O_NOFOLLOW` refuses to open a symlink, which is why the lock is opened with it -- somebody who
#: can drop a link in `~/.palmar/run/` should not get us to lock a file of their choosing. Windows
#: has no such flag; the directory is already 0700-equivalent by profile ACL there (docs/windows.md).
NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
