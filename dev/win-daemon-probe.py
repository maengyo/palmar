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


D = {}


def _import():
    import palmar.daemon as d
    D["d"] = d


if wall("import palmar.daemon", _import):
    d = D["d"]
    say("    PALMAR_DIR =", getattr(d, "PALMAR_DIR", "?"))
    say("    Pty =", getattr(d, "Pty", None).__name__ if getattr(d, "Pty", None) else "?")

    wall("setup_palmar_dir (dirs, shim, 0700)", lambda: d.setup_palmar_dir())
    # **Not called separately.** setup_palmar_dir already takes it, and LOCK_FH keeps that descriptor
    # open for the daemon's life -- so asking again in the same process is asking for a lock we are
    # holding, and being refused is correct. That refusal was read here as "Windows cannot lock" for
    # three rounds; the four-variant check above is what showed msvcrt was never the problem.
    say("    the lock: taken inside setup_palmar_dir above, and still held")

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
    wall("platform: asyncio add_signal_handler", _loop)

    def _sigchld():
        import signal
        if not hasattr(signal, "SIGCHLD"):
            raise AttributeError("signal has no SIGCHLD on Windows -- pane death comes from console EOF")
    wall("platform: signal.SIGCHLD exists", _sigchld)

    def _reader():
        """**Step 2, the largest piece left.** add_reader has no Windows equivalent, so this is the
        thing the daemon still cannot do: be told when there is output, instead of sitting in a read."""
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

    def _threaded():
        """**Step 2, through the daemon's own path.** start_reading has to put a thread on it, the
        bytes have to arrive in `pending` on the loop, and stop_reading has to park the thread rather
        than let memory fill."""
        import asyncio

        async def go():
            p = D["pane"]
            p.start_reading()
            if not p.reading:
                raise RuntimeError("start_reading did not take")
            if p._thread is None or not p._thread.is_alive():
                raise RuntimeError("no reader thread is running")
            # **Watch `produced`, not `pending`.** pending is a 5ms staging area -- _flush empties it
            # into the ring and out to whoever is attached -- so looking there 100ms later finds
            # nothing and says the thread read nothing. It had read plenty. `produced` is the total
            # that has gone out and only grows.
            for _ in range(40):
                if p.produced:
                    break
                await asyncio.sleep(0.1)
            if not p.produced:
                raise RuntimeError("the thread produced nothing in 4s (thread alive=%r, can_read=%r)"
                                   % (p._thread.is_alive(), p._can_read.is_set()))
            say("    produced:", p.produced, "bytes")
            p.stop_reading()
            if p._can_read.is_set():
                raise RuntimeError("stop_reading did not park the thread")
            say("    stop_reading parked it")

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(go())
        finally:
            loop.close()
    wall("the daemon reads on a thread (step 2)", _threaded)

    def _bytes():
        p = D.get("pane")
        if p is None:
            raise RuntimeError("no pane to read from")
        got = p.pty.read(65536)
        if not got:
            raise RuntimeError("the pane printed nothing")
        say("    first bytes:", repr(got[:60]))
    wall("a pane prints something", _bytes)


    pane = D.get("pane")
    if pane is not None:
        # **Guarded, because this is where a whole run was lost.** die() drained the console before
        # closing it, and a blocking read on a quiet pane never comes back -- the job hit its
        # six-minute timeout and every check after this never ran (2026-09-14).
        wall("close a pane without hanging", lambda: pane.die("probe done"))


head("result")
if WALLS:
    say(NO, "walls, in the order they were hit:")
    for i, w in enumerate(WALLS, 1):
        say("     %d. %s" % (i, w))
    say("    ^ that order is the plan. docs/windows.md has the list; this has the sequence.")
else:
    say(OK, "nothing in this probe stopped it -- widen the probe")
