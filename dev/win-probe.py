#!/usr/bin/env python3
"""win-probe — answers, on a real Windows machine, the questions this Mac cannot.

    python dev\\win-probe.py

palmar's daemon is POSIX to the bone and the native port is #29. That port is being written on a
machine that cannot run a line of it, so the project's rule — nothing is done without a measurement
— has to be kept from somewhere else. This is that somewhere: one run prints the facts the port
needs, and a person (or CI) pastes the output back.

**It reports what it finds rather than what it expects.** The API calls below are written from
documentation, not from having run them, so every section prints what it actually got — including
the shape of pywinpty's own API. A wrong guess here should show up as a printed surprise, not as a
crash that hides the rest.

Nothing here writes outside a temp directory, and nothing runs longer than its own deadline: a hang
on a CI runner burns paid minutes.

This is a dev tool. It does not ship in the package.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import traceback

# The Windows console starts on a legacy code page and `print` dies on anything outside it — the
# first run of this probe was killed mid-section by a UnicodeEncodeError on a circled digit.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEADLINE = 20.0          # seconds any single read loop may take
OK, NO, HM = "  ok ", "  NO ", "  ?? "


def head(t):
    print("\n" + t)
    print("-" * len(t))


def say(mark, *a):
    print(mark + " " + " ".join(str(x) for x in a))


def guarded(name):
    """Run a section, never let it take the rest of the probe down with it."""
    def deco(fn):
        head(name)
        try:
            fn()
        except Exception:
            say(NO, "this section raised — everything below still ran")
            for line in traceback.format_exc().strip().split("\n")[-4:]:
                print("      " + line)
        return fn
    return deco


# **Windows only, and it says so rather than wasting a minute finding out.** On a POSIX box
# `python -m palmar` below would start a real daemon and sit there until its timeout — a minute of
# a metered CI runner spent proving nothing.
if sys.platform != "win32":
    raise SystemExit(
        "win-probe answers questions about Windows and has to run there.\n"
        "  This is %s. Start it from the Actions tab, or: gh workflow run windows-probe.yml" % sys.platform
    )


# ── the machine ────────────────────────────────────────────────────────────────────────────
head("the machine")
import platform
say(OK, "windows   ", platform.platform())
say(OK, "release   ", platform.win32_ver())
say(OK, "python    ", platform.python_version(), platform.machine(), sys.executable)
say(OK, "codepage  ", "console in/out below (949 is Korean Windows, 65001 is UTF-8)")
try:
    import ctypes
    k = ctypes.WinDLL("kernel32")
    say(OK, "  GetConsoleCP      ", k.GetConsoleCP())
    say(OK, "  GetConsoleOutputCP", k.GetConsoleOutputCP())
    say(OK, "CreatePseudoConsole present:", hasattr(k, "CreatePseudoConsole"))
except Exception as e:
    say(HM, "could not ask kernel32 —", e)


# ── #29 item 0: palmar's own guard ─────────────────────────────────────────────────────────
@guarded("item 0 — does `python -m palmar` say a sentence instead of a stack?")
def _item0():
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r = subprocess.run([sys.executable, "-m", "palmar"], cwd=repo, capture_output=True,
                       text=True, timeout=20)
    out = (r.stdout + r.stderr).strip()
    say(OK if r.returncode == 1 else NO, "exit code", r.returncode, "(want 1)")
    say(OK if "Traceback" not in out else NO, "traceback lines:", out.count("Traceback"), "(want 0)")
    say(OK if "does not run natively on Windows" in out else NO, "the sentence is there")
    for line in out.split("\n")[:5]:
        print("      | " + line)


@guarded("does `import palmar` still work (this is what lets dev-stub run here)")
def _imp():
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r = subprocess.run([sys.executable, "-c", "import palmar; print(palmar.__version__, palmar.PROTOCOL)"],
                       cwd=repo, capture_output=True, text=True, timeout=60)
    say(OK if r.returncode == 0 else NO, "import palmar ->", (r.stdout or r.stderr).strip()[:80])


# ── pywinpty: what is actually there ───────────────────────────────────────────────────────
PTY = None


@guarded("pywinpty — the API as it really is, not as the docs read")
def _api():
    global PTY
    try:
        import winpty
    except ImportError as e:
        say(NO, "pywinpty is not installed —", e)
        say(HM, "everything below needs it: pip install pywinpty")
        return
    say(OK, "version   ", getattr(winpty, "__version__", "(no __version__)"))
    say(OK, "module has", ", ".join(sorted(n for n in dir(winpty) if not n.startswith("_")))[:200])
    PTY = getattr(winpty, "PTY", None)
    if PTY is None:
        say(NO, "no winpty.PTY — the low-level class the port planned to use is not there")
        return
    meth = sorted(n for n in dir(PTY) if not n.startswith("_"))
    say(OK, "PTY methods", ", ".join(meth)[:220])
    for want in ("spawn", "read", "write", "set_size", "isalive"):
        say(OK if want in meth else NO, "  PTY." + want)
    # **Is the read blocking?** This decides whether plat_win.py needs a reader thread, and neither
    # the README nor PyPI says. Look for a blocking flag on the signature first.
    try:
        import inspect
        for n in ("__init__", "spawn", "read", "write", "set_size", "get_exitstatus"):
            f = getattr(PTY, n, None)
            if f is not None:
                say(OK, "  PTY.%-14s %s" % (n, inspect.signature(f)))
    except Exception as e:
        say(HM, "signatures unavailable —", e)
    # The high-level class as well: it may be the better fit, and one run costs real minutes.
    PP = getattr(winpty, "PtyProcess", None)
    if PP is not None:
        say(OK, "PtyProcess methods", ", ".join(sorted(n for n in dir(PP) if not n.startswith("_")))[:220])
        try:
            import inspect
            say(OK, "  PtyProcess.spawn", str(inspect.signature(PP.spawn)))
            say(OK, "  PtyProcess.read ", str(inspect.signature(PP.read)))
        except Exception:
            pass


def open_pty(cmd, cols=100, rows=30, cmdline=None):
    """Open one pseudoconsole. **Checks the spawn** — a failed spawn and a silent one used to look
    the same, and every downstream answer was really "nothing was read"."""
    p = PTY(cols, rows)
    got = p.spawn(cmd, cmdline=cmdline) if cmdline else p.spawn(cmd)
    say(OK if got is not False else NO, "spawn(%s) -> %r · alive %s · pid %s"
        % (os.path.basename(cmd), got, p.isalive(), getattr(p, "pid", "?")))
    return p


def drain(p, seconds=3.0, stop=None):
    """Read until `stop` appears, or until the deadline. **Never exits early on quiet.**

    The previous version stopped after 0.4 s with nothing coming in, which on a cold pane meant it
    returned ConPTY's own handshake and quit before cmd.exe had printed a single character — and
    every later section then measured that emptiness instead of the thing it was asking about.
    Quiet is not the end of anything here; the sentinel is."""
    buf = b""
    end = time.time() + min(seconds, DEADLINE)
    while time.time() < end:
        try:
            chunk = p.read()
        except Exception as e:
            say(HM, "  read raised —", type(e).__name__, e)
            break
        if chunk:
            buf += chunk if isinstance(chunk, bytes) else str(chunk).encode("utf-8", "replace")
            if stop and stop in buf:
                break
        else:
            time.sleep(0.01)
    return buf


SEQ = [0]


def command(p, line, seconds=8.0):
    """Send one command and read until **its own** end marker comes back.

    Everything before used a timer and hoped. A marker is the only way to know the shell got as far
    as the end of what we sent — and when it does not come back, that is itself the finding."""
    SEQ[0] += 1
    mark = "PALMARDONE%d" % SEQ[0]
    p.write("%s & echo %s\r\n" % (line, mark))
    out = drain(p, seconds, stop=mark.encode())
    if mark.encode() not in out:
        say(NO, "  the shell never echoed %s back — it did not run: %r" % (mark, out[-120:]))
    return out


def settle(p, seconds=8.0):
    """Wait for a cold shell to actually reach a prompt, proved by a round trip."""
    drain(p, 1.5)
    out = command(p, "echo PROBEREADY", seconds)
    return b"PROBEREADY" in out


# ── the stream questions (#29 items 3, 4, 5, 15) ───────────────────────────────────────────
SHELL = os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe")


@guarded("does input reach the shell at all? (everything below is worthless if not)")
def _alive():
    if PTY is None:
        say(HM, "skipped: no PTY class")
        return
    p = open_pty(SHELL)
    say(OK if settle(p) else NO, "a round trip through cmd.exe completes")
    try:
        p.write("exit\r\n"); drain(p, 1.0)
    except Exception:
        pass


@guarded("items 3 & 5 — the opening bytes, and the window-title terminator")
def _stream():
    if PTY is None:
        return
    p = open_pty(SHELL)
    first = drain(p, 2.0)
    say(OK, "opening bytes  ", repr(first[:120]))
    say(HM, "  these land in the ring and get replayed to a late browser — _absorb may have to")
    say(HM, "  filter them the way it filters 1049.")
    if not settle(p):
        say(NO, "  the shell is not answering; the title result below means nothing")
        return
    out = command(p, "title palmar-probe")
    i = out.find(b"\x1b]0;")
    if i < 0:
        say(HM, "cmd's `title` produced no ESC]0; — ConPTY may not translate it")
        say(HM, "  raw:", repr(out[:200]))
    else:
        seg = out[i:i + 80]
        say(OK, "title sequence ", repr(seg[:60]))
        say(OK, "terminator     ", "BEL" if b"\x07" in seg else ("ST (ESC backslash)" if b"\x1b\\" in seg else "neither"))
    # An agent sets the title itself rather than through `title`, so ask that way too — it is the
    # path #38 actually depends on.
    out2 = command(p, 'echo \x1b]0;palmar-osc\x07')
    j = out2.find(b"]0;palmar-osc")
    say(OK if j >= 0 else HM, "an OSC written by the program survives:", j >= 0)
    if j >= 0:
        say(OK, "  terminator   ", repr(out2[j + 13:j + 16]))
    try:
        p.write("exit\r\n"); drain(p, 1.0)
    except Exception:
        pass


@guarded("item 4 — alt-screen: does ESC[?1049h get through ConPTY?")
def _alt():
    if PTY is None:
        return
    # **Spawn the writer directly instead of typing at a prompt.** Quoting a PowerShell one-liner
    # through a pty is its own source of failure, and the last run could not tell that apart from
    # "the marker did not survive".
    ps = "powershell.exe"
    line = ('powershell.exe -NoLogo -NoProfile -Command '
            '"[Console]::Write([char]27 + \'[?1049h\'); '
            '[Console]::Write(\'MARKERBETWEEN\'); '
            '[Console]::Write([char]27 + \'[?1049l\')"')
    q = open_pty(ps, cmdline=line)
    c = drain(q, 6.0, stop=b"MARKERBETWEEN")
    say(OK if b"MARKERBETWEEN" in c else NO, "the writer ran at all:", b"MARKERBETWEEN" in c)
    on = b"\x1b[?1049h" in c
    off = b"\x1b[?1049l" in c
    say(OK if on else NO, "ESC[?1049h survives the pipe:", on)
    say(OK if off else NO, "ESC[?1049l survives the pipe:", off)
    if not on:
        say(HM, "  -> palmar's `alt` can never be true on Windows. _absorb would never fire, and")
        say(HM, "     the reconnect-restore path for alt panes (⑨) does not exist there.")
        say(HM, "  raw:", repr(c[-300:]))

    # And what a real full-screen program does, which is a different question.
    p = open_pty(SHELL)
    if settle(p):
        tmp = os.path.join(tempfile.gettempdir(), "palmar_probe_lines.txt")
        with open(tmp, "w") as fh:
            for i in range(400):
                fh.write("line %d\n" % i)
        p.write("more %s\r\n" % tmp)
        b2 = drain(p, 4.0)
        say(OK if b"\x1b[?1049" in b2 else HM,
            "a real pager: h x%d  l x%d" % (b2.count(b"\x1b[?1049h"), b2.count(b"\x1b[?1049l")))
        try:
            p.write("q"); p.write("exit\r\n"); drain(p, 1.0)
        except Exception:
            pass


@guarded("item 15 — does Korean survive the round trip? (bytes only; IME needs a person)")
def _korean():
    if PTY is None:
        return
    p = open_pty(SHELL)
    if not settle(p):
        say(NO, "the shell is not answering")
        return
    command(p, "chcp 65001")
    out = command(p, "echo 한글도 잘 되나")
    txt = out.decode("utf-8", "replace")
    say(OK if "한글도 잘 되나" in txt else NO, "echoed back intact")
    if "한글도 잘 되나" not in txt:
        say(HM, "  raw tail:", repr(out[-200:]))
    try:
        p.write("exit\r\n"); drain(p, 1.0)
    except Exception:
        pass


@guarded("item 8 — how much of a paste actually arrives, and above what size does it stop?")
def _paste():
    if PTY is None:
        return
    # **A staircase, not one big write.** 208 KB in a single write got the receiver exactly one line
    # and then nothing for ninety seconds — which is not slowness, it is loss, and it is the same
    # shape as #1 on macOS (a short write whose return value was dropped). What the port needs is
    # not "does 200 KB work" but **where the edge is**, so it knows what to chunk to.
    import winpty
    tmpdir = tempfile.gettempdir()
    script = os.path.join(tmpdir, "palmar_paste_count.py")
    with open(script, "w") as fh:
        fh.write(
            "import sys, time, traceback\n"
            "out = sys.argv[1]\n"
            "log = []\n"
            "def put():\n"
            "    open(out, 'w').write('\\n'.join(log))\n"
            "try:\n"
            "    n = b = 0\n"
            "    t0 = time.time()\n"
            "    quiet = 0\n"
            # An empty readline on a console means 'nothing yet', not 'end'. Breaking on it made
            # every size report zero in 0.0s. Only long silence after data counts as the end.
            "    while time.time() - t0 < 25:\n"
            "        line = sys.stdin.buffer.readline()\n"
            "        if not line:\n"
            "            quiet += 1\n"
            "            if n and quiet > 300:\n"
            "                log.append('quiet after %d lines' % n)\n"
            "                break\n"
            "            time.sleep(0.01)\n"
            "            continue\n"
            "        quiet = 0\n"
            "        if b'PALMARENDOFPASTE' in line:\n"
            "            log.append('SENTINEL')\n"
            "            break\n"
            "        n += 1\n"
            "        b += len(line)\n"
            "        if n % 25 == 0:\n"
            "            log.append('at %d after %.1fs' % (n, time.time() - t0))\n"
            "            put()\n"
            "    log.append('OK %d %d %.1f' % (b, n, time.time() - t0))\n"
            "    put()\n"
            "except Exception:\n"
            "    log.append('ERR ' + traceback.format_exc())\n"
            "    put()\n")

    for lines in (12, 100, 800, 2600):        # ~1 KB, 8 KB, 64 KB, 208 KB
        out = os.path.join(tmpdir, "palmar_paste_%d.txt" % lines)
        try:
            os.remove(out)
        except OSError:
            pass
        blob = ("x" * 79 + "\n") * lines
        p = winpty.PtyProcess.spawn([sys.executable, script, out], dimensions=(50, 200))
        time.sleep(1.2)
        try:
            p.write(blob)
            p.write("PALMARENDOFPASTE\r\n")
        except Exception as e:
            say(NO, "%6d lines (%6d B): write raised — %s" % (lines, len(blob), e))
            continue
        got, secs, sent = None, None, False
        for _ in range(90):
            time.sleep(0.25)
            if not os.path.exists(out):
                continue
            raw = open(out).read()
            sent = sent or "SENTINEL" in raw
            ok = [l for l in raw.split("\n") if l.startswith("OK ")]
            if ok:
                f = ok[-1].split()
                got, secs = int(f[2]), float(f[3])
                break
        if got is None:
            say(NO, "%6d lines (%6d B): the receiver never finished" % (lines, len(blob)))
        else:
            say(OK if got == lines else NO,
                "%6d lines (%6d B): %d arrived in %.1fs%s"
                % (lines, len(blob), got, secs, "" if got == lines else "   <- LOST %d" % (lines - got)))
        try:
            p.kill()
        except Exception:
            pass


@guarded("item 16 — how many processes does an idle shell have?")
def _idle():
    # A learned floor, not a hardcoded 1 (#29 §1.4). Report what each shell idles at.
    for name, exe in (("cmd", os.environ.get("COMSPEC", "cmd.exe")),
                      ("powershell", "powershell.exe"),
                      ("pwsh", "pwsh.exe")):
        try:
            r = subprocess.run([exe, "-c" if name != "cmd" else "/c", "echo hi"],
                               capture_output=True, text=True, timeout=30)
            say(OK if r.returncode == 0 else HM, "%-11s present (%s)" % (name, exe))
        except Exception:
            say(HM, "%-11s not on this machine" % name)
    say(HM, "the per-shell idle count needs a job object around a live pane — that is step 5,")
    say(HM, "not this probe. What matters here is which shells exist to test against.")


print("\n" + "=" * 72)
print("Paste everything above into https://github.com/maengyo/palmar/issues/29")
print("=" * 72)

# ══ the port's own questions (added 2026-09-11) ═════════════════════════════════════════════
# Everything above was about whether ConPTY carries what palmar reads. These are about whether the
# daemon's *shape* survives: its event loop, how a pane dies, its lock, and its file permissions.
# Each one decides a design choice that cannot be decided from a Mac.

@guarded("port — which asyncio loop, and does add_reader work at all")
def _loop():
    import asyncio
    say("    default policy:", type(asyncio.get_event_loop_policy()).__name__)
    loop = asyncio.new_event_loop()
    say("    loop:", type(loop).__name__)
    # **This is the one that decides the read path.** If add_reader refuses, every pane needs a
    # reader thread instead of a callback on the loop.
    import socket
    a, b = socket.socketpair()
    try:
        loop.add_reader(a.fileno(), lambda: None)
        loop.remove_reader(a.fileno())
        say(OK, "add_reader on a socket: works")
    except Exception as e:
        say(NO, "add_reader on a socket:", type(e).__name__, str(e)[:70])
    finally:
        a.close(); b.close()
    # A ConPTY handle is not a socket. Try a plain pipe, which is the closest stand-in.
    r, w = os.pipe()
    try:
        loop.add_reader(r, lambda: None)
        loop.remove_reader(r)
        say(OK, "add_reader on a pipe: works")
    except Exception as e:
        say(NO, "add_reader on a pipe:", type(e).__name__, str(e)[:70],
            "<- panes would need a reader thread")
    finally:
        os.close(r); os.close(w)
    try:
        import signal as sig
        loop.add_signal_handler(sig.SIGINT, lambda: None)
        say(OK, "add_signal_handler: works")
    except Exception as e:
        say(NO, "add_signal_handler:", type(e).__name__, "<- Ctrl-C needs signal.signal instead")
    loop.close()


# **Defined above its caller on purpose**: @guarded runs the section the moment it decorates it,
# so anything the section calls has to exist by then. It did not, and the section died with a
# NameError while every other one ran (2026-09-11).
def _python_sleepers():
    """How many python processes are sitting in that sleep. tasklist is on every Windows."""
    try:
        out = subprocess.run(["tasklist", "/fi", "imagename eq python.exe"],
                             capture_output=True, text=True, timeout=15).stdout
        return out.lower().count("python.exe")
    except Exception:
        return -1


@guarded("port — killing a shell: do its children die with it?")
def _tree():
    """palmar closes a pane by killing the shell. On POSIX the agent inside dies with it. If it does
    not here, every pane needs a Job Object and that has to be decided before the port, not after."""
    import ctypes
    # a shell that starts a long-lived child, so there is something to orphan
    parent = subprocess.Popen(["cmd.exe", "/c", "start", "/b", sys.executable, "-c",
                               "import time; time.sleep(60)"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    before = _python_sleepers()
    say("    sleeping python processes after spawn:", before)
    parent.kill(); parent.wait(timeout=10)
    time.sleep(1.5)
    after = _python_sleepers()
    say("    after killing the parent:", after)
    if after >= before and before > 0:
        say(NO, "the child outlived its parent — a pane needs a Job Object to take its tree with it")
    elif before > 0:
        say(OK, "the child died with the parent")
    else:
        say(HM, "could not create a child to orphan; inconclusive")
    # Can we even make a Job Object from the standard library + ctypes?
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        h = k32.CreateJobObjectW(None, None)
        say(OK if h else NO, "CreateJobObjectW ->", h, "(ctypes alone, no pywin32)")
        if h:
            k32.CloseHandle(h)
    except Exception as e:
        say(NO, "CreateJobObjectW:", type(e).__name__, str(e)[:70])


@guarded("port — the single-instance lock, and whether it survives a kill")
def _lock():
    """flock releases when the holder dies, however it dies. Whatever replaces it must do the same,
    or a crashed daemon locks its own home out forever."""
    import msvcrt
    box = tempfile.mkdtemp(prefix="palmar-lock-")
    path = os.path.join(box, "lock")
    open(path, "wb").close()
    f = open(path, "r+b")
    try:
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        say(OK, "msvcrt.locking took an exclusive lock")
    except OSError as e:
        say(NO, "msvcrt.locking:", e)
        return
    # a second process must fail to take it
    code = ("import msvcrt,sys\n"
            "f=open(sys.argv[1],'r+b')\n"
            "try:\n"
            "    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1); print('TOOK')\n"
            "except OSError: print('REFUSED')\n")
    r = subprocess.run([sys.executable, "-c", code, path], capture_output=True, text=True, timeout=20)
    say(OK if "REFUSED" in r.stdout else NO, "a second process ->", r.stdout.strip() or r.stderr.strip()[:60])
    # and it must come back when the holder is killed outright
    holder = subprocess.Popen([sys.executable, "-c",
                               "import msvcrt,sys,time\n"
                               "f=open(sys.argv[1],'r+b')\n"
                               "msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)\n"
                               "print('HELD', flush=True); time.sleep(60)\n", path],
                              stdout=subprocess.PIPE, text=True)
    f.close()                                  # let go so the child can take it
    time.sleep(1.0)
    holder.kill(); holder.wait(timeout=10)
    time.sleep(0.5)
    g = open(path, "r+b")
    try:
        msvcrt.locking(g.fileno(), msvcrt.LK_NBLCK, 1)
        say(OK, "the lock came back after the holder was killed")
    except OSError as e:
        say(NO, "the lock did NOT come back after a kill:", e, "<- a crash would wedge the home")
    finally:
        g.close()


@guarded("port — what 0600 actually does to a file here")
def _perms():
    """palmar writes run/key and run/token 0600 and the threat model is 'another account on this
    machine'. chmod is close to a no-op on Windows, so what protects them is the question."""
    box = tempfile.mkdtemp(prefix="palmar-perm-")
    path = os.path.join(box, "key")
    with open(path, "w") as fh:
        fh.write("secret")
    os.chmod(path, 0o600)
    say("    st_mode after chmod 0600: %s" % oct(os.stat(path).st_mode & 0o777))
    r = subprocess.run(["icacls", path], capture_output=True, text=True, timeout=20)
    for line in (r.stdout or "").strip().splitlines()[:6]:
        say("    icacls:", line.strip()[:100])
    prof = os.environ.get("USERPROFILE", "")
    if prof:
        r2 = subprocess.run(["icacls", prof], capture_output=True, text=True, timeout=20)
        say("    %USERPROFILE% ACL (this is what really guards ~/.palmar):")
        for line in (r2.stdout or "").strip().splitlines()[:5]:
            say("      ", line.strip()[:100])


@guarded("port — which shell would palmar launch, and can we read a child's cwd")
def _shell_and_cwd():
    say("    COMSPEC:", os.environ.get("COMSPEC", "(unset)"))
    import shutil as sh
    for name in ("powershell.exe", "pwsh.exe", "cmd.exe", "bash.exe", "wsl.exe"):
        where = sh.which(name)
        say("   ", ("%-16s" % name), where or "-")
    # A pane's cwd feeds the restore file. If there is no cheap way, Windows returns None and the
    # restore offer simply reopens at home — worth knowing before promising the feature.
    say("    reading another process's cwd without pywin32 is NtQueryInformationProcess + PEB;")
    say("    not attempted here — the question is whether it is worth it, not whether it is possible.")

