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


def open_pty(cmd, cols=100, rows=30):
    """Open one pseudoconsole on `cmd`. **Checks that the spawn worked** — the first run of this
    probe read zero bytes from every pane and reported that as "no markers seen", when the real
    answer was that nothing had been asked in a way that could answer."""
    p = PTY(cols, rows)
    got = p.spawn(cmd)
    say(OK if got is not False else NO, "spawn(%r) returned %r" % (cmd, got))
    try:
        say(OK if p.isalive() else NO, "  isalive", p.isalive(), "· pid", getattr(p, "pid", "?"))
    except Exception as e:
        say(HM, "  isalive raised —", e)
    return p


def drain(p, seconds=3.0, stop=None):
    """Read until quiet, the deadline, or `stop` appears.

    **`PTY.read` takes no length.** Its real signature is `(self, /, blocking=False)` — the first
    run passed 4096 into the `blocking` slot and polled with a 50 ms sleep, which is how a shell
    that prints a banner immediately came back as zero bytes. Non-blocking with a tight poll: a
    blocking read that never returns would hold a metered runner for the whole job timeout."""
    buf = b""
    end = time.time() + min(seconds, DEADLINE)
    empty = 0
    while time.time() < end:
        try:
            chunk = p.read()
        except Exception as e:
            say(HM, "  read raised —", type(e).__name__, e)
            break
        if chunk:
            empty = 0
            buf += chunk if isinstance(chunk, bytes) else str(chunk).encode("utf-8", "replace")
            if stop and stop in buf:
                break
        else:
            empty += 1
            if empty > 40 and buf:
                break              # it printed, then went quiet — that is the end of the burst
            time.sleep(0.01)
    return buf


# ── the stream questions (#29 items 3, 4, 5, 15) ───────────────────────────────────────────
@guarded("items 3 & 5 — the opening bytes, and the window-title terminator")
def _stream():
    if PTY is None:
        say(HM, "skipped: no PTY class")
        return
    shell = os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe")
    p = open_pty(shell)
    first = drain(p, 2.0)
    say(OK, "shell     ", shell)
    say(OK, "first 160 bytes", repr(first[:160]))
    # item 5: a title is set, then look at what ends it
    p.write("prompt $P$G\r\n")
    p.write("title palmar-probe\r\n")
    b = drain(p, 2.0)
    i = b.find(b"\x1b]0;")
    if i < 0:
        say(HM, "no ESC]0; seen — either the title went nowhere or it uses another OSC number")
        say(HM, "  raw:", repr(b[:200]))
    else:
        seg = b[i:i + 80]
        say(OK, "title sequence", repr(seg[:60]))
        say(OK, "terminator    ", "BEL (\\x07)" if b"\x07" in seg else ("ST (ESC\\\\)" if b"\x1b\\" in seg else "neither — look above"))
    try:
        p.write("exit\r\n"); drain(p, 1.0)
    except Exception:
        pass


@guarded("item 4 — does a full-screen app emit ESC[?1049h / l ?")
def _alt():
    if PTY is None:
        say(HM, "skipped: no PTY class")
        return
    shell = os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe")
    p = open_pty(shell)
    drain(p, 1.5)
    # `more` on a long file is the cheapest full-screen thing every Windows has.
    tmp = os.path.join(tempfile.gettempdir(), "palmar_probe_lines.txt")
    with open(tmp, "w") as fh:
        for i in range(400):
            fh.write("line %d\n" % i)
    p.write("more %s\r\n" % tmp)
    b = drain(p, 3.0)
    on, off = b.count(b"\x1b[?1049h"), b.count(b"\x1b[?1049l")
    say(OK if on or off else HM, "a real full-screen app: ESC[?1049h x%d   ESC[?1049l x%d" % (on, off))
    if not (on or off):
        say(HM, "  -> alt-screen is invisible here. palmar's `alt` would always be false on Windows,")
        say(HM, "     and the ⑨ reconnect-restore path for alt panes would not exist. That is a")
        say(HM, "     contract fact (#29 item 4), so paste this line back either way.")
    try:
        p.write("q"); p.write("exit\r\n"); drain(p, 1.0)
    except Exception:
        pass

    # **The question `_absorb` actually asks is whether the marker survives the pipe**, not whether
    # some app on this machine happens to use it. Emit it deliberately and look for it coming back.
    # If it does not survive, palmar can never see alt-screen on Windows no matter what runs there.
    q = open_pty("powershell.exe -NoLogo -NoProfile")
    drain(q, 2.5)
    q.write("[Console]::Write(\"`e[?1049h\"); [Console]::Write(\"MARKER-BETWEEN\"); [Console]::Write(\"`e[?1049l\")\r\n")
    c = drain(q, 3.0)
    say(OK if b"MARKER-BETWEEN" in c else HM, "the deliberate write came back:", b"MARKER-BETWEEN" in c)
    say(OK if b"\x1b[?1049h" in c else NO,
        "ESC[?1049h survives the pipe:", b"\x1b[?1049h" in c)
    say(OK if b"\x1b[?1049l" in c else NO,
        "ESC[?1049l survives the pipe:", b"\x1b[?1049l" in c)
    if b"\x1b[?1049h" not in c:
        say(HM, "  raw around the marker:", repr(c[-260:]))
    try:
        q.write("exit\r\n"); drain(q, 1.0)
    except Exception:
        pass


@guarded("item 15 — does Korean survive the round trip? (bytes only; IME needs a person)")
def _korean():
    if PTY is None:
        say(HM, "skipped: no PTY class")
        return
    shell = os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe")
    p = open_pty(shell)
    drain(p, 1.5)
    p.write("chcp 65001\r\n")
    drain(p, 1.5)
    p.write("echo 한글도 잘 되나\r\n")
    b = drain(p, 2.0)
    txt = b.decode("utf-8", "replace")
    say(OK if "한글도 잘 되나" in txt else NO, "echoed back intact")
    if "한글도 잘 되나" not in txt:
        say(HM, "  raw tail:", repr(b[-160:]))
    try:
        p.write("exit\r\n"); drain(p, 1.0)
    except Exception:
        pass


@guarded("item 8 — does a 200 KB paste arrive whole?")
def _paste():
    if PTY is None:
        say(HM, "skipped: no PTY class")
        return
    shell = os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe")
    p = open_pty(shell, cols=200, rows=50)
    drain(p, 1.5)
    blob = ("x" * 79 + "\n") * 2600          # ~208 KB
    t = time.time()
    try:
        p.write(blob)
    except Exception as e:
        say(NO, "write raised on a big payload —", e)
        return
    say(OK, "wrote %d bytes in %.2fs without raising" % (len(blob), time.time() - t))
    say(HM, "whether the child RECEIVED all of it needs the daemon; this only shows write() survives")
    try:
        p.write("\x03"); drain(p, 1.0)
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
