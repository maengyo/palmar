"""The pseudo-terminal, on POSIX — the same shape as palmar/conpty.py.

**This exists so the daemon has one seam instead of thirty-nine.** `pty.fork`, `os.read`, `ioctl`
and `tcgetpgrp` were called from a dozen places in daemon.py; Windows has none of them, and a port
that answers each call site separately is a port with `sys.platform` scattered through it. Both
files now offer the same seven operations, and the daemon picks one of them once.

**The two are not symmetric, and the difference is real rather than cosmetic.** Here a master fd
goes into the event loop and the loop says when to read; there is no such handle on Windows and no
way to wait on a ConPTY, so that side reads on a thread. `blocking` says which world a Pty lives in,
and it is the only thing the daemon has to branch on.
"""
from __future__ import annotations

import fcntl
import os
import pty
import signal
import struct
import sys
import termios

if sys.platform == "win32":                      # pragma: no cover - POSIX only by construction
    raise ImportError("palmar.posixpty is for POSIX; Windows uses palmar.conpty")


class PosixPty:
    """One pty and the process in it. `pty.fork` makes the child a session leader, so its process
    group is its own pid — which is what makes `foreground_is_shell` answerable here and not there."""

    #: The event loop can wait on this one, so the daemon reads when told rather than on a thread.
    blocking = False

    def __init__(self):
        self.master = None
        self.pid = None
        self.closed = False
        #: Has the foreground process group ever differed from the shell's. A shell without job
        #: control keeps its own group forever, and that means **unknowable**, not "nothing is
        #: running" — so this is trusted only after it has been seen to differ at least once.
        self.fg_varied = False

    def spawn(self, argv, env=None, cwd=None, rows: int = 24, cols: int = 80) -> None:
        """Fork a shell into a new pty. `argv` is a list; **the caller chooses it and no client
        ever can** (#14, principle 4)."""
        pid, master = pty.fork()
        if pid == 0:                             # child — never returns
            try:
                if cwd:
                    os.chdir(cwd)
            except OSError:
                pass
            try:
                os.execvpe(argv[0], argv, env if env is not None else os.environ)
            except Exception:
                os._exit(127)
        self.pid, self.master = pid, master
        self.resize(rows, cols)
        # Non-blocking, because the loop hands us readability and we drain until it says no more.
        os.set_blocking(master, False)

    # ── bytes ──────────────────────────────────────────────────────────
    def read(self, n: int = 65536) -> bytes:
        """Up to `n` bytes. **Raises BlockingIOError when there is nothing right now** — the daemon
        reads that as "drained", not as an error — and b"" or OSError when the child is gone."""
        return os.read(self.master, n)

    def write(self, data: bytes) -> int:
        return os.write(self.master, data)

    def resize(self, rows: int, cols: int) -> None:
        fcntl.ioctl(self.master, termios.TIOCSWINSZ,
                    struct.pack("HHHH", max(1, int(rows)), max(1, int(cols)), 0, 0))

    def fileno(self):
        """The master fd, for add_reader/add_writer. **None on Windows** — that side has no handle
        the loop can wait on, which is why `blocking` exists."""
        return self.master

    # ── what is running in there ───────────────────────────────────────
    def foreground_is_shell(self):
        """True when the shell itself holds the terminal — nothing is running. None when unknowable.

        Windows has no foreground process group at all, so its answer is always None and the status
        lights fall back to what the title and the output say."""
        try:
            fg = os.tcgetpgrp(self.master)
        except OSError:
            return None
        if fg != self.pid:
            self.fg_varied = True
            return False
        return True if self.fg_varied else None

    # ── life and death ─────────────────────────────────────────────────
    def alive(self) -> bool:
        if self.pid is None:
            return False
        try:
            os.kill(self.pid, 0)
            return True
        except OSError:
            return False

    def hangup(self) -> None:
        """Ask it to go. The shell's children go with it because they share its session."""
        try:
            os.kill(self.pid, signal.SIGHUP)
        except (OSError, TypeError):
            pass

    def kill(self) -> None:
        try:
            os.kill(self.pid, signal.SIGKILL)
        except (OSError, TypeError):
            pass

    def exit_status(self):
        """The child's exit code, or None while it runs. The daemon learns about death from SIGCHLD
        rather than by asking, so this is here for the seam's sake and for the probes."""
        try:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
        except (OSError, TypeError):
            return None
        if pid == 0:
            return None
        return os.WEXITSTATUS(status) if os.WIFEXITED(status) else -os.WTERMSIG(status)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.master is not None:
            try:
                os.close(self.master)
            except OSError:
                pass
            self.master = None


def default_shell(env=None) -> list:
    """What palmar opens. `$SHELL`, and `/bin/sh` when there is none — **the daemon hard-codes this
    and no client can pick it** (#14)."""
    env = os.environ if env is None else env
    return [env.get("SHELL") or "/bin/sh"]
