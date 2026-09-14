"""How far does the daemon get on Windows today? (#29)

**Written so the machine orders the work instead of me.** docs/windows.md lists 41 platform-bound
sites and a four-step plan, but a list is not an order -- what matters is which one stops the
process first, then the next. This walks the real start-up path with `PALMAR_WINDOWS_ANYWAY` set
and reports each wall in turn, so one CI round replaces a round of guessing.

It changes nothing outside its own temporary HOME, and it never leaves a daemon running.

    python dev\\win-daemon-probe.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import traceback

OK, NO, HM = " ok ", " NO ", " ?? "
WALLS = []


def say(*a):
    print("  " + " ".join(str(x) for x in a), flush=True)


def head(t):
    print()
    print(t)
    print("-" * len(t))


def wall(name, fn):
    """Run one step. A failure is the **result**, not a crash -- print it and go on, because the
    step after it usually fails for its own reason and both are worth knowing in one round."""
    try:
        fn()
        say(OK, name)
        return True
    except BaseException as e:            # SystemExit included: the refusal is what we are measuring
        WALLS.append(name)
        say(NO, name, "--", type(e).__name__ + ":", str(e).split("\n")[0][:140])
        for line in traceback.format_exc().strip().split("\n")[-3:]:
            print("        " + line)
        return False


# The daemon's sentences are Korean and a Windows console is not UTF-8 -- printing the first refusal
# killed this probe before it could report it (2026-09-14). It has to survive what it is reporting on.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

if sys.platform != "win32":
    raise SystemExit("win-daemon-probe runs on Windows. This is " + sys.platform)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOME = tempfile.mkdtemp(prefix="palmar-windaemon-")
os.environ["HOME"] = HOME
os.environ["USERPROFILE"] = HOME
os.environ["PALMAR_WINDOWS_ANYWAY"] = "1"

head("how far does the daemon get on Windows?")
say("    HOME =", HOME)
say("    python", sys.version.split()[0])

D = {}


def _import():
    import palmar.daemon as d
    D["d"] = d


if wall("import palmar.daemon", _import):
    d = D["d"]
    say("    PALMAR_DIR =", getattr(d, "PALMAR_DIR", "?"))
    say("    Pty =", getattr(d, "Pty", None).__name__ if getattr(d, "Pty", None) else "?")

    wall("setup_palmar_dir (dirs, shim, 0700)", lambda: d.setup_palmar_dir())
    wall("acquire_single_instance_lock", lambda: d.acquire_single_instance_lock())

    def _spawn():
        # The real constructor, with the real shell -- this is where pty.fork used to be and where
        # ConPty now is. `canvas` is required, so give it one that does not have to exist yet.
        D["pane"] = d.Session("probe", os.getcwd(), 80, 24, "probe-canvas")
    wall("open a pane (Session -> ConPty.spawn)", _spawn)

    def _loop():
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            loop.add_signal_handler(2, lambda: None)
        finally:
            loop.close()
    wall("asyncio add_signal_handler", _loop)

    def _sigchld():
        import signal
        if not hasattr(signal, "SIGCHLD"):
            raise AttributeError("signal has no SIGCHLD on Windows -- pane death comes from console EOF")
    wall("signal.SIGCHLD exists", _sigchld)

    def _reader():
        """**The read path, which is step 2 and the largest piece left.** add_reader has no Windows
        equivalent, so this is what the daemon cannot yet do: be told when there is output."""
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            p = D.get("pane")
            fd = p.pty.fileno() if p else None
            if fd is None:
                raise NotImplementedError("ConPty has no fileno -- the loop cannot wait on it (blocking=%r)"
                                          % getattr(p.pty, "blocking", "?"))
            loop.add_reader(fd, lambda: None)
        finally:
            loop.close()
    wall("the loop can wait for output (step 2)", _reader)

    def _bytes():
        """Does a pane actually produce bytes? The blocking read is what step 2 has to wrap."""
        p = D.get("pane")
        if p is None:
            raise RuntimeError("no pane to read from")
        got = b""
        end = time.time() + 6
        while time.time() < end and len(got) < 16:
            chunk = p.pty.read(65536)
            if not chunk:
                break
            got += chunk
        if not got:
            raise RuntimeError("the pane printed nothing in 6s")
        say("    first bytes:", repr(got[:60]))
    wall("a pane prints something", _bytes)

    pane = D.get("pane")
    if pane is not None:
        try:
            pane.die("probe done")
        except Exception:
            pass

def _locking():
    """**Which msvcrt.locking actually takes on a fresh file?**

    take() locks one byte at offset 0, and the lock file is empty at that moment -- the pid line is
    written after. That came back as PermissionError, which the caller reads as "somebody else holds
    it", so a first daemon refused to start on the grounds that it was already running. Guessing
    which variant works costs a round each; asking for all of them costs one."""
    import msvcrt
    box = tempfile.mkdtemp(prefix="palmar-lockprobe-")

    def attempt(label, size, offset):
        path = os.path.join(box, "lk-%s" % label)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        if size:
            os.write(fd, b"x" * size)
        os.lseek(fd, offset, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError as e:
            say(NO, "%-28s %s" % (label, type(e).__name__ + ": " + str(e)))
            os.close(fd)
            return None
        # And it has to actually exclude a second holder, or it is not a lock.
        fd2 = os.open(path, os.O_RDWR)
        os.lseek(fd2, offset, os.SEEK_SET)
        try:
            msvcrt.locking(fd2, msvcrt.LK_NBLCK, 1)
            say(HM, "%-28s taken, but a SECOND holder got it too -- not exclusive" % label)
            taken = False
        except OSError:
            say(OK, "%-28s taken, and a second holder is refused" % label)
            taken = True
        os.close(fd2)
        os.lseek(fd, offset, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError as e:
            say(HM, "     ...but unlocking raised %s" % type(e).__name__)
        os.close(fd)
        return taken

    attempt("empty file, offset 0", 0, 0)
    attempt("1 byte written, offset 0", 1, 0)
    attempt("empty file, offset 2^30", 0, 1 << 30)
    attempt("64 bytes, offset 2^30", 64, 1 << 30)


wall("which msvcrt.locking works", _locking)

head("result")
if WALLS:
    say(NO, "walls, in the order they were hit:")
    for i, w in enumerate(WALLS, 1):
        say("     %d. %s" % (i, w))
    say("    ^ that order is the plan. docs/windows.md has the list; this has the sequence.")
else:
    say(OK, "nothing in this probe stopped it -- widen the probe")
