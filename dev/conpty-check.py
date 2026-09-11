#!/usr/bin/env python3
"""Does palmar/conpty.py work? Answered on Windows, because it cannot be answered anywhere else.

    python dev\\conpty-check.py

Every claim this file makes about ConPTY was written on a Mac from documentation. This runs it on a
real Windows and prints what happened. **The three byte tests are the ones that matter** — they are
exactly what pywinpty failed, and failing them here would mean the whole reason for writing
palmar/conpty.py was not achieved.

Like dev/win-probe.py: each section is guarded, prints what it found rather than what was expected,
and child scripts are written to files with no backslash in them.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import traceback

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OK, NO, HM = "  ok ", "  NO ", "  ?? "
NL = chr(10)
FAILED = []


def say(*a):
    # **flush every line.** On a CI runner stdout is a pipe and therefore buffered, so when the
    # first run hung nothing at all had been printed — the log showed the hang and none of the
    # progress leading to it (2026-09-11).
    print(" ".join(str(x) for x in a), flush=True)


def head(t):
    print(); print(t); print("-" * len(t))


def guarded(name):
    def deco(fn):
        head(name)
        try:
            fn()
        except Exception:
            FAILED.append(name)
            say(NO, "this section raised — the rest still ran")
            for line in traceback.format_exc().strip().split(NL)[-5:]:
                print("      " + line)
        return fn
    return deco


if sys.platform != "win32":
    raise SystemExit("conpty-check runs on Windows. This is " + sys.platform +
                     ".\n  gh workflow run windows-probe.yml")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BOX = tempfile.mkdtemp(prefix="palmar-conpty-")


def script(name, lines):
    """A child script on disk. No backslashes anywhere — that cost three CI runs in win-probe."""
    path = os.path.join(BOX, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(NL.join(lines) + NL)
    return path


def drain(p, secs, until=None):
    """Read a ConPTY for `secs`, on a thread, and give back what arrived.

    **read() blocks and there is no way to wait on that handle** — that is the whole reason the
    Windows daemon will read on a thread. The first version of this file looped `while time.time()
    < end: p.read()`, which cannot time out at all: a read that never returns never lets the
    deadline be looked at, and the check hung for eight minutes and printed nothing.

    The thread is a daemon thread and is simply abandoned if it is still blocked. That is also what
    the daemon does: a pane's reader dies with the process, not with the pane."""
    import threading
    out, done = [], threading.Event()

    def pump():
        try:
            while True:
                chunk = p.read()
                if not chunk:
                    break
                out.append(chunk)
                if until and until in b"".join(out):
                    break
        except Exception:
            pass
        finally:
            done.set()

    t = threading.Thread(target=pump, daemon=True)
    t.start()
    done.wait(secs)
    if not done.is_set():
        say("    (the reader is still blocked after %.0fs — abandoning it)" % secs)
    return b"".join(out)


def run(lines, secs=20.0, rows=50, cols=200, name="child.py", until=None):
    """Run a python child in a ConPTY and return every byte it produced."""
    from palmar import conpty
    path = script(name, lines)
    p = conpty.ConPty()
    p.spawn('"%s" -u "%s"' % (sys.executable, path), rows=rows, cols=cols)
    try:
        return drain(p, secs, until)
    finally:
        p.close()


@guarded("spawn, step by step")
def _steps():
    """Every call's return and the error behind it. Cheaper than guessing from a Mac."""
    import ctypes
    import ctypes.wintypes as wintypes
    from ctypes import byref
    from palmar import conpty as C
    k = C.kernel32
    sa = C.SECURITY_ATTRIBUTES(ctypes.sizeof(C.SECURITY_ATTRIBUTES), None, True)
    in_r, in_w = wintypes.HANDLE(), wintypes.HANDLE()
    out_r, out_w = wintypes.HANDLE(), wintypes.HANDLE()
    say("    CreatePipe(in) ->", bool(k.CreatePipe(byref(in_r), byref(in_w), byref(sa), 0)))
    say("    CreatePipe(out)->", bool(k.CreatePipe(byref(out_r), byref(out_w), byref(sa), 0)))
    hpc = wintypes.HANDLE()
    try:
        k.CreatePseudoConsole(C.COORD(133, 37), in_r, out_w, 0, byref(hpc))
        say("    CreatePseudoConsole -> hpc =", hpc.value)
    except OSError as e:
        say(NO, "CreatePseudoConsole raised", e)
        return
    need = ctypes.c_size_t(0)
    k.InitializeProcThreadAttributeList(None, 1, 0, byref(need))
    say("    attribute list wants", need.value, "bytes")
    buf = (ctypes.c_ubyte * need.value)()
    si = C.STARTUPINFOEXW()
    si.StartupInfo.cb = ctypes.sizeof(C.STARTUPINFOEXW)
    si.lpAttributeList = ctypes.cast(buf, ctypes.c_void_p)
    say("    si.lpAttributeList =", si.lpAttributeList, "· addressof(buf) =", ctypes.addressof(buf))
    ok = k.InitializeProcThreadAttributeList(si.lpAttributeList, 1, 0, byref(need))
    say("    InitializeProcThreadAttributeList ->", bool(ok), "err", ctypes.get_last_error())
    ok = k.UpdateProcThreadAttribute(si.lpAttributeList, 0,
                                     ctypes.c_size_t(C.PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE),
                                     hpc, ctypes.c_size_t(ctypes.sizeof(wintypes.HANDLE)),
                                     None, None)
    say("    UpdateProcThreadAttribute ->", bool(ok), "err", ctypes.get_last_error())
    say("    sizeof(STARTUPINFOEXW) =", ctypes.sizeof(C.STARTUPINFOEXW),
        "· sizeof(STARTUPINFOW) =", ctypes.sizeof(C.STARTUPINFOW))

    # **Do the whole thing here, inline.** Every call above returns True and the child still lands
    # outside the console, so the question is whether ConPty.spawn is wrong or the API is being
    # understood wrongly. Running CreateProcess right here, with these exact handles, separates them.
    path = script("who.py", [
        "import ctypes, shutil, sys",
        "k = ctypes.WinDLL('kernel32')",
        "sz = shutil.get_terminal_size((0, 0))",
        "msg = 'INLINE size=%dx%d hwnd=%s' % (sz.columns, sz.lines, k.GetConsoleWindow())",
        "sys.stdout.write(msg + chr(10))",
        "sys.stdout.flush()",
    ])
    pi = C.PROCESS_INFORMATION()
    cmd = '"%s" -u "%s"' % (sys.executable, path)
    ok = k.CreateProcessW(None, ctypes.create_unicode_buffer(cmd), None, None, False,
                          C.EXTENDED_STARTUPINFO_PRESENT, None, None, byref(si), byref(pi))
    say("    CreateProcessW ->", bool(ok), "err", ctypes.get_last_error(), "pid", pi.dwProcessId)
    k.CloseHandle(out_w)          # let the console own the far end, or out_r never sees EOF
    k.CloseHandle(in_r)
    out, end = [], time.time() + 6
    import threading
    hit = threading.Event()

    def pump():
        try:
            while True:
                b = (ctypes.c_char * 4096)()
                got = wintypes.DWORD(0)
                if not k.ReadFile(out_r, b, 4096, byref(got), None):
                    break
                if not got.value:
                    break
                out.append(bytes(b[:got.value]))
                if b"INLINE" in b"".join(out):
                    break
        except Exception:
            pass
        finally:
            hit.set()

    threading.Thread(target=pump, daemon=True).start()
    hit.wait(6)
    blob = b"".join(out)
    say("    inline pipe got %r" % blob[-90:])
    if b"INLINE size=133x37" in blob:
        say(OK, "**the inline spawn works** — the bug is in ConPty.spawn, not in the API")
    elif b"INLINE" in blob:
        say(NO, "the child is in a console, but not ours:", blob[-60:])
    else:
        say(NO, "**the inline spawn fails the same way** — the API use itself is wrong")
    k.ClosePseudoConsole(hpc)
    for h in (in_w, out_r):
        k.CloseHandle(h)


@guarded("it imports, and this Windows has ConPTY")
def _import():
    from palmar import conpty
    say(OK, "imported palmar.conpty")
    say(OK if conpty.available() else NO, "CreatePseudoConsole present:", conpty.available())
    say("    default shell here:", conpty.default_shell())


@guarded("where is the child actually attached?")
def _attached():
    """**The one signal that settles it.** If the child is in our pseudo-console it sees the size we
    asked for; if the attribute silently did not apply it inherits the runner's console and sees
    that instead — which is what "its output shows up in the CI log and not in our pipe" looks like.

    Printed by the child into whatever console it has, so the answer arrives even when our pipe
    stays empty."""
    from palmar import conpty
    path = script("size.py", [
        "import os, sys, shutil",
        "sz = shutil.get_terminal_size((0, 0))",
        "sys.stdout.write('SIZE=%d x %d' % (sz.columns, sz.lines) + chr(10))",
        "sys.stdout.flush()",
    ])
    p = conpty.ConPty()
    p.spawn('"%s" -u "%s"' % (sys.executable, path), rows=37, cols=133)
    say("    asked for 133 x 37 · child pid", p.pid)
    got = drain(p, 10.0, until=b"SIZE=")
    p.close()
    say("    our pipe got %r" % got[-70:])
    if b"SIZE=133 x 37" in got:
        say(OK, "the child is in OUR pseudo-console")
    elif b"SIZE=" in got:
        say(NO, "**the child is in some other console** — the attribute did not apply")
    else:
        say(HM, "nothing came through our pipe; look for SIZE= in the raw log above/below")


@guarded("a child starts and its output comes back")
def _hello():
    got = run(["import sys", "sys.stdout.write('MARKERyes')", "sys.stdout.flush()"],
              secs=15.0, until=b"MARKERyes")
    say("    read back %r" % got[-60:])
    say(OK if b"MARKERyes" in got else NO,
        "the child ran" if b"MARKERyes" in got else "**nothing came back**")


@guarded("bytes — a NUL survives (pywinpty dropped it)")
def _nul():
    got = run(["import sys",
               "sys.stdout.buffer.write(b'[A' + bytes([0]) + b'B]')",
               "sys.stdout.flush()"], secs=15.0, until=b"B]")
    i = got.find(b"[A")
    say("    read back %r" % (got[i:i + 6] if i >= 0 else got[-40:]))
    ok = bytes([0]) in got
    say(OK if ok else NO, "NUL:", "survives" if ok else "**still dropped**")
    if not ok:
        FAILED.append("NUL")


@guarded("bytes — invalid UTF-8 passes through (pywinpty made it U+FFFD)")
def _bad():
    got = run(["import sys",
               "sys.stdout.buffer.write(b'[' + bytes([255, 254]) + b']')",
               "sys.stdout.flush()"], secs=15.0, until=b"]")
    i = got.find(b"[")
    say("    read back %r" % (got[i:i + 6] if i >= 0 else got[-40:]))
    ok = bytes([255, 254]) in got
    say(OK if ok else NO, "FF FE:", "passed through" if ok else "**altered**")
    if not ok:
        FAILED.append("invalid UTF-8")


@guarded("bytes — 210 KB of Korean arrives whole (pywinpty destroyed 8)")
def _korean():
    got = run(["import sys",
               "sys.stdout.buffer.write((chr(0xD55C) * 70000).encode('utf-8'))",
               "sys.stdout.write(chr(10) + 'END' + chr(10))",
               "sys.stdout.flush()"], secs=60.0, until=b"END")
    han = got.count(chr(0xD55C).encode("utf-8"))
    fffd = got.count(chr(0xFFFD).encode("utf-8"))
    say("    got %d 한, %d U+FFFD, END seen: %s, %d bytes" %
        (han, fffd, b"END" in got, len(got)))
    ok = fffd == 0 and han >= 69900
    say(OK if ok else NO,
        "byte-exact" if ok else "**%d 한 and %d U+FFFD — not byte-exact**" % (han, fffd))
    if not ok:
        FAILED.append("Korean")


@guarded("input reaches the child")
def _input():
    from palmar import conpty
    path = script("echo.py", ["import sys",
                              "line = sys.stdin.readline()",
                              "sys.stdout.write('SAW:' + line.strip() + chr(10))",
                              "sys.stdout.flush()"])
    p = conpty.ConPty()
    p.spawn('"%s" -u "%s"' % (sys.executable, path))
    out, end = [], time.time() + 20
    try:
        time.sleep(1.0)
        p.write(b"hello-from-palmar" + bytes([13]))
        while time.time() < end:
            chunk = p.read()
            if chunk:
                out.append(chunk)
                if b"SAW:" in b"".join(out):
                    break
            elif not p.alive():
                break
    finally:
        p.close()
    got = b"".join(out)
    say("    read back %r" % got[-70:])
    say(OK if b"SAW:hello-from-palmar" in got else NO, "the child saw what we wrote")


@guarded("closing a pane takes the whole tree with it")
def _tree():
    """The job object's reason for existing. Without it, killing a shell leaves its agent running —
    measured on this same runner before this file was written."""
    from palmar import conpty
    path = script("spawner.py", [
        "import subprocess, sys, time",
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])",
        "sys.stdout.write('SPAWNED' + chr(10))",
        "sys.stdout.flush()",
        "time.sleep(120)",
    ])
    before = _sleepers()
    p = conpty.ConPty()
    p.spawn('"%s" -u "%s"' % (sys.executable, path))
    end = time.time() + 20
    while time.time() < end:
        if b"SPAWNED" in p.read():
            break
    time.sleep(1.5)
    during = _sleepers()
    p.close()
    time.sleep(2.0)
    after = _sleepers()
    say("    sleeping python processes: before %d, with the pane open %d, after close %d"
        % (before, during, after))
    if during <= before:
        say(HM, "the grandchild never appeared; inconclusive")
    elif after <= before:
        say(OK, "the tree died with the pane")
    else:
        say(NO, "**%d survived** — the job object is not doing its work" % (after - before))
        FAILED.append("tree")


def _sleepers():
    import subprocess
    try:
        out = subprocess.run(["tasklist", "/fi", "imagename eq python.exe"],
                             capture_output=True, text=True, timeout=20).stdout
        return out.lower().count("python.exe")
    except Exception:
        return -1


@guarded("resize does not break the stream")
def _resize():
    from palmar import conpty
    path = script("chatty.py", ["import sys, time",
                                "for i in range(40):",
                                "    sys.stdout.write('line%d' % i + chr(10))",
                                "    sys.stdout.flush()",
                                "    time.sleep(0.05)"])
    p = conpty.ConPty()
    p.spawn('"%s" -u "%s"' % (sys.executable, path), rows=24, cols=80)
    out, end = [], time.time() + 20
    resized = False
    try:
        while time.time() < end:
            chunk = p.read()
            if chunk:
                out.append(chunk)
                if not resized and b"line5" in b"".join(out):
                    p.resize(40, 120)
                    resized = True
            elif not p.alive():
                break
    finally:
        p.close()
    got = b"".join(out)
    say("    resized mid-output:", resized, "· saw line39:", b"line39" in got)
    say(OK if (resized and b"line39" in got) else NO,
        "the stream survived a resize" if b"line39" in got else "**output stopped after the resize**")


head("result")
if FAILED:
    say(NO, "failed:", ", ".join(sorted(set(FAILED))))
    raise SystemExit(1)
say(OK, "everything above passed")
