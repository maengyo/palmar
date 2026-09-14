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
            raise AttributeError("signal has no SIGCHLD on Windows")
    wall("signal.SIGCHLD exists", _sigchld)

    pane = D.get("pane")
    if pane is not None:
        try:
            pane.die("probe done")
        except Exception:
            pass

head("result")
if WALLS:
    say(NO, "walls, in the order they were hit:")
    for i, w in enumerate(WALLS, 1):
        say("     %d. %s" % (i, w))
    say("    ^ that order is the plan. docs/windows.md has the list; this has the sequence.")
else:
    say(OK, "nothing in this probe stopped it -- widen the probe")
