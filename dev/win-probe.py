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


@guarded("item 8 — does a 200 KB paste arrive whole?")
def _paste():
    if PTY is None:
        return
    # **Let the receiving end count, and write the count to a file.** Two earlier tries failed for
    # the same reason in different clothes: `write()` not raising proves nothing (the macOS bug this
    # mirrors, #1, was a short write that dropped bytes in silence), and `find /c /v ""` does not
    # read a pty's stdin — what came back was 200 KB of console echo and cursor moves.
    # Python in the pane reads stdin to EOF and writes what it got somewhere this probe can read,
    # so nothing has to be parsed out of the echo at all.
    tmpdir = tempfile.gettempdir()
    outfile = os.path.join(tmpdir, "palmar_paste_count.txt")
    script = os.path.join(tmpdir, "palmar_paste_count.py")
    try:
        os.remove(outfile)
    except OSError:
        pass
    # **The path goes in as an argument, not baked into the source.** The first attempt wrote
    # `open(r%r, ...)` with %r on a Windows path, which doubles every backslash inside a raw string
    # — the receiver died with exit 1 and the probe could only report that it "never reached EOF".
    # It also writes the traceback into the same file, so a failure explains itself next time.
    with open(script, "w") as fh:
        fh.write(
            "import sys, traceback\n"
            "out = sys.argv[1]\n"
            "try:\n"
            "    n = b = 0\n"
            "    while True:\n"
            "        line = sys.stdin.buffer.readline()\n"
            "        if not line or b'PALMARENDOFPASTE' in line:\n"
            "            break\n"
            "        n += 1\n"
            "        b += len(line)\n"
            "    open(out, 'w').write('OK %d %d' % (b, n))\n"
            "except Exception:\n"
            "    open(out, 'w').write('ERR ' + traceback.format_exc())\n")
    n = 2600
    blob = ("x" * 79 + "\n") * n            # ~208 KB
    # **The high-level class, because it takes a list.** With PTY.spawn(appname, cmdline=...) the
    # run before this one produced `SyntaxError: Non-UTF-8 code ... in file python.exe`: pywinpty
    # puts appname at the front itself, so repeating the executable in cmdline shifted argv by one
    # and Python was handed its own binary as a script. PtyProcess.spawn(argv) has no quoting and
    # no argv[0] convention to get wrong — worth knowing for the port, which needs cwd and env too.
    import winpty
    p = winpty.PtyProcess.spawn([sys.executable, script, outfile], dimensions=(50, 200))
    say(OK, "spawned via PtyProcess.spawn(argv) · alive %s" % p.isalive())
    time.sleep(1.5)                          # let the interpreter come up
    t = time.time()
    try:
        p.write(blob)
    except Exception as e:
        say(NO, "write raised on a big payload —", e)
        return
    dt = time.time() - t
    # **A sentinel, not Ctrl-Z.** The run before this one showed the receiver still alive with the
    # payload echoing back: `\x1a` is an end-of-input signal to a console in line mode, and through
    # a ConPTY pipe it is just another byte. So the payload ends with a line the receiver watches for.
    p.write("PALMARENDOFPASTE\r\n")
    say(OK, "wrote %d bytes in %.2fs without raising" % (len(blob), dt))
    for _ in range(80):                      # wait for the child to finish writing the file
        if os.path.exists(outfile):
            break
        time.sleep(0.25)
    tail = b""
    try:
        tail = p.read(4096).encode("utf-8", "replace")
    except Exception:
        pass
    if not os.path.exists(outfile):
        say(NO, "the receiver never wrote its count")
        say(HM, "  alive:", p.isalive(), "· exit:", getattr(p, "exitstatus", "?"))
        say(HM, "  what the pane showed:", repr(tail[-400:]))
        return
    raw = open(outfile).read()
    if not raw.startswith("OK "):
        say(NO, "the receiver raised:")
        for line in raw.strip().split("\n")[-5:]:
            print("      " + line)
        return
    got = raw.split()
    nbytes, nlines = int(got[1]), int(got[2])
    say(OK if nlines == n else NO, "the receiver got %d lines of %d" % (nlines, n))
    say(OK, "  and %d bytes (sent %d — a difference here is CRLF translation, not loss)"
        % (nbytes, len(blob)))
    if nlines != n:
        say(HM, "  -> input is being truncated. That is #1 again, from the other side.")


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
