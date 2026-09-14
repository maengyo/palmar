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


def _attrs():
    """**Every `os.X` / `signal.X` / `stat.X` the source names, checked against this platform at once.**

    Found one at a time, each of these costs a CI round and a person's afternoon: `os.getuid`,
    `os.fchmod`, `os.O_NOFOLLOW`, and then `os.O_NONBLOCK` buried inside git_branch, which made the
    directory rail come up empty with nothing on screen to say why. Reading the source and asking the
    platform is a few milliseconds and finds the rest of them together."""
    import ast
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    import os as _os
    import signal as _signal
    import stat as _stat
    mods = {"os": _os, "signal": _signal, "stat": _stat}
    missing = []
    looked = 0
    for name in sorted(os.listdir(os.path.join(repo, "palmar"))):
        if not name.endswith(".py"):
            continue
        # **Skip what this platform refuses to import at all.** posixpty raises ImportError on
        # Windows by design, so every POSIX name in it is expected and reporting them buries the
        # ones that matter -- seven false alarms against one real find, the first time this ran.
        try:
            __import__("palmar." + name[:-3])
        except ImportError:
            say("    (skipping %s -- this platform does not import it)" % name)
            continue
        except Exception:
            pass
        path = os.path.join(repo, "palmar", name)
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
                continue
            mod = mods.get(node.value.id)
            if mod is None:
                continue
            looked += 1
            if not hasattr(mod, node.attr):
                missing.append("%s:%d  %s.%s" % (name, node.lineno, node.value.id, node.attr))
    say("    checked %d references across palmar/*.py" % looked)
    if missing:
        for m in sorted(set(missing)):
            say(NO, "   ", m)
        raise RuntimeError("%d name(s) this platform does not have" % len(set(missing)))
    say(OK, "every os/signal/stat name in the source exists here")


wall("names the source uses that this platform lacks", _attrs)


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


def _serve():
    """**The whole daemon, in its own process, answering HTTP.**

    Not `main()` in this one: the probe has already taken the home lock above, and a second
    `setup_palmar_dir` in the same process would be refused -- correctly. A subprocess also means the
    real entry point, `cli()` included, which is what a person actually runs."""
    import socket
    import subprocess
    import urllib.error
    import urllib.request

    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    home = tempfile.mkdtemp(prefix="palmar-serve-")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    env = dict(os.environ, HOME=home, USERPROFILE=home, PALMAR_WINDOWS_ANYWAY="1",
               PYTHONPATH=repo, PYTHONIOENCODING="utf-8")
    say("    starting a daemon on port", port)
    proc = subprocess.Popen([sys.executable, "-m", "palmar", "--no-browser", "--port", str(port)],
                            cwd=repo, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace")
    url_file = os.path.join(home, ".palmar", "run", "url")
    try:
        url = None
        end = time.time() + 25
        while time.time() < end:
            if os.path.exists(url_file):
                with open(url_file, encoding="utf-8") as fh:
                    url = fh.read().strip()
                break
            if proc.poll() is not None:
                out, err = proc.communicate()
                raise RuntimeError("it exited (%s): %s" % (proc.returncode, (err or out).strip()[-300:]))
            time.sleep(0.3)
        if not url:
            raise RuntimeError("no run/url in 25s")
        base = url.split("/?")[0]
        say("    it says it is at", base)
        req = urllib.request.Request(url, headers={"Origin": base})
        with urllib.request.urlopen(req, timeout=15) as r:
            body = r.read()
            if r.status != 200:
                raise RuntimeError("the page came back %d" % r.status)
        say("    GET / ->", r.status, "%d bytes" % len(body),
            "· looks like the app" if b"PALMAR_TOKEN" in body else "· **no token in it**")
        if b"PALMAR_TOKEN" not in body:
            raise RuntimeError("the page has no token script -- it is not palmar's index.html")

        # And a real terminal through the real API, which is the thing the page would do next.
        tok_file = os.path.join(home, ".palmar", "run", "token")
        with open(tok_file, encoding="utf-8") as fh:
            token = fh.read().strip()
        import json as _json
        # **Inside HOME.** The daemon only opens terminals under roots it will admit, and the checkout
        # is on another drive from this probe's temporary HOME -- so asking for it came back 400, and
        # that refusal is the feature working (2026-09-14).
        body = _json.dumps({"cwd": home, "name": "probe"}).encode()
        req = urllib.request.Request(base + "/api/sessions?token=" + token, data=body,
                                     headers={"Origin": base, "Content-Type": "application/json"},
                                     method="POST")
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                made = _json.loads(r.read())
        except urllib.error.HTTPError as e:
            # Say **what it said**. A bare "400" sends the next round guessing, which it did.
            raise RuntimeError("POST /api/sessions -> %d %s" % (e.code, e.read()[:200].decode("utf-8", "replace")))
        say("    POST /api/sessions ->", r.status, "· id", made.get("id"), "· status", made.get("status"))
        req = urllib.request.Request(base + "/api/sessions?token=" + token, headers={"Origin": base})
        with urllib.request.urlopen(req, timeout=20) as r:
            listed = _json.loads(r.read())
        rows = listed if isinstance(listed, list) else listed.get("sessions", [])
        if not any(x.get("id") == made.get("id") for x in rows):
            raise RuntimeError("the session was made but is not in the list")
        say("    it is in the list ·", len(rows), "session(s)")
    finally:
        subprocess.run([sys.executable, "-m", "palmar", "--stop"], cwd=repo, env=env,
                       capture_output=True, timeout=60)
        try:
            proc.wait(timeout=20)
        except Exception:
            proc.kill()
        for pipe in (proc.stdout, proc.stderr):
            try:
                pipe.close()
            except Exception:
                pass


wall("the whole daemon serves a page", _serve)

head("result")
if WALLS:
    say(NO, "walls, in the order they were hit:")
    for i, w in enumerate(WALLS, 1):
        say("     %d. %s" % (i, w))
    say("    ^ that order is the plan. docs/windows.md has the list; this has the sequence.")
else:
    say(OK, "nothing in this probe stopped it -- widen the probe")
