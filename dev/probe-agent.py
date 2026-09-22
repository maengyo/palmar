#!/usr/bin/env python3
"""probe-agent — **measures** whether palmar's status lights come on for this agent.

    python3 dev/probe-agent.py <command> [args...]
    e.g.  python3 dev/probe-agent.py <agent command>

It runs the command on a real PTY and **lets you use it as usual** — type your prompts as always,
give it work, and quit the agent when you are done. All the while it records **the exact bytes**
palmar would see, and reports them on the way out.

Why this is needed: palmar asks the agent nothing. It holds the PTY, so all it sees is the bytes
flowing past (docs/protocol.md "제목으로 읽기"). So "do the lights come on for this agent?" is
not something to guess at — **you just run it once**.

What is measured here is exactly what palmar decides on:
  · how often the window title (OSC 0/1/2) changes — twice or more within 3s means "working"
  · how long the quiet stretches are — the value that gauges whether the fallback is needed

and, since 2026-09-23, the two things that might yet answer **"it is waiting for you"** for an
agent that has neither hooks nor a title (docs/reports.md). Neither is screen reading; both are
terminal state, the same class of signal as OSC 133:
  · **the bell** (`\a`) — the oldest "look at me" there is, and the one an agent is most likely to
    already ring. OSC sequences end in a bell too, so those are subtracted.
  · **the cursor** (DECTCEM, `ESC[?25h` / `ESC[?25l`) — most TUIs hide it while drawing and show it
    while waiting for a key, so the last toggle before a quiet stretch is worth knowing.

**Take it to an approval prompt and leave it there a few seconds.** That moment is the whole
question: whatever comes out then is what palmar could read; if nothing does, the honest answer is
that this agent cannot be read and the only exact path left is a hook.

This file is a dev tool. It does not go into the product (`palmar/`).
"""
from __future__ import annotations

import os
import pty
import re
import select
import signal
import sys
import termios
import time
import tty
from fcntl import ioctl
from struct import pack

WINDOW_S = 3.0      # the same window as palmar/__init__.py
BUSY_N = 2          # this many title changes inside it means "it is running"
OSC = re.compile(rb"\x1b\][012];([^\x07\x1b]{0,255})(?:\x07|\x1b\\)")

#: Queries the agent throws at the terminal on startup. A real terminal answers — we can just pass
#: them through (a real terminal is behind us), but we note in the report what it asked.
QUERIES = [
    (b"\x1b]10;?", "foreground colour"),
    (b"\x1b]11;?", "background colour"),
    (b"\x1b[>c",   "secondary device attributes"),
    (b"\x1b[?u",   "kitty keyboard protocol"),
]


def winsize(fd: int) -> bytes:
    try:
        return ioctl(fd, termios.TIOCGWINSZ, b"\0" * 8)
    except OSError:
        return pack("HHHH", 24, 80, 0, 0)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__.strip())
        return 2
    argv = sys.argv[1:]

    pid, master = pty.fork()
    if pid == 0:
        os.execvp(argv[0], argv)

    ioctl(master, termios.TIOCSWINSZ, winsize(sys.stdin.fileno()))
    signal.signal(signal.SIGWINCH,
                  lambda *_: ioctl(master, termios.TIOCSWINSZ, winsize(sys.stdin.fileno())))
    # **Report on the way out even when signalled to die.** Else a 25s run ended by kill shows none.
    def _bail(*_):
        raise KeyboardInterrupt
    for _sig in (signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(_sig, _bail)
        except (OSError, ValueError):
            pass

    t0 = time.monotonic()
    titles: list[tuple[float, str]] = []      # (time, title) — only ones that actually changed
    marks: list[float] = []                   # the times bytes arrived
    bells: list[float] = []                   # (time) each bell that was not an OSC terminator
    cursor: list[tuple[float, bool]] = []     # (time, shown) — DECTCEM, only when it changed
    asked: set[str] = set()
    total = 0
    last_title = None

    watch_stdin = True
    old = None
    try:
        old = termios.tcgetattr(sys.stdin)
        tty.setraw(sys.stdin.fileno())
    except (termios.error, ValueError):
        pass                                   # running under a pipe — the recording still works

    try:
        while True:
            watch = ([sys.stdin] if watch_stdin else []) + [master]
            try:
                r, _, _ = select.select(watch, [], [], 0.2)
            except (OSError, ValueError):
                break
            if watch_stdin and sys.stdin in r:
                data = os.read(sys.stdin.fileno(), 65536)
                if not data:
                    # My input ended, **not the agent.** Stopping here measures nothing and
                    # finishes at 0 bytes under a pipe (that is how this was wrong at first).
                    watch_stdin = False
                    continue
                os.write(master, data)
            if master in r:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                os.write(sys.stdout.fileno(), chunk)   # **shown as-is** — just use it as usual
                now = time.monotonic() - t0
                total += len(chunk)
                marks.append(now)
                for q, name in QUERIES:
                    if q in chunk:
                        asked.add(name)
                osc_bells = 0
                for m in OSC.finditer(chunk):
                    if m.group(0).endswith(b"\x07"):
                        osc_bells += 1
                    t = m.group(1).decode("utf-8", "replace")
                    if t != last_title:
                        last_title = t
                        titles.append((now, t))
                # **A bell that is not the end of an OSC.** Every OSC here finishes with one, so
                # counting raw \a would report the agent ringing every time it set its title.
                rang = chunk.count(b"\x07") - osc_bells
                for _ in range(max(0, rang)):
                    bells.append(now)
                # DECTCEM. Only the changes: a TUI that re-hides an already hidden cursor on every
                # frame would otherwise read as a signal when it is a redraw.
                for seq, shown in ((b"\x1b[?25h", True), (b"\x1b[?25l", False)):
                    if seq in chunk and (not cursor or cursor[-1][1] != shown):
                        cursor.append((now, shown))
            try:
                if os.waitpid(pid, os.WNOHANG)[0]:
                    break
            except ChildProcessError:
                break
    except KeyboardInterrupt:
        pass
    finally:
        if old is not None:
            try:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old)
            except Exception:
                pass
        try:
            os.close(master)
        except OSError:
            pass
        try:
            os.kill(pid, signal.SIGHUP)
        except ProcessLookupError:
            pass

    report(argv, time.monotonic() - t0, total, titles, marks, asked, bells, cursor)
    return 0


def report(argv, dur, total, titles, marks, asked, bells=(), cursor=()) -> None:
    # **Keep a file copy too.** A full-screen TUI restores the screen as it exits and buries what
    # was printed here — you measure it all and then cannot find what to look at (it happened).
    lines = []
    def say(x=""):
        lines.append(x)
        print(x, file=sys.stderr)
    say()
    say("─" * 72)
    say("probe-agent — %s  (%.1fs, %s bytes)" % (" ".join(argv), dur, f"{total:,}"))
    say("─" * 72)

    if asked:
        say("terminal queries it sent: " + ", ".join(sorted(asked)))
        say("  (a probe that does not answer these can hang the TUI — this one passes")
        say("   them through to your real terminal, so what you saw is what it does.)")
        say()

    # Count the stretches where the title is spinning, by the same rule palmar uses
    busy = []
    for i, (t, _) in enumerate(titles):
        n = sum(1 for tt, _ in titles if 0 <= t - tt <= WINDOW_S)
        if n >= BUSY_N:
            busy.append(t)
    spans = []
    for t in busy:
        if spans and t - spans[-1][1] <= WINDOW_S:
            spans[-1][1] = t
        else:
            spans.append([t, t])

    # **The two that could answer "waiting for you".** Printed before the title, because for an
    # agent that sets no title they are the whole report.
    say("bell (\\a, not counting the ones that end an OSC): %d" % len(bells))
    if bells:
        say("  at " + ", ".join("%.1fs" % t for t in bells[:10]) + (" …" if len(bells) > 10 else ""))
        say("  → palmar could read this as \"it wants you\". It is the oldest such signal there is.")
    else:
        say("  → nothing to read here.")
    say("cursor (DECTCEM): %d change%s" % (len(cursor), "" if len(cursor) == 1 else "s"))
    if cursor:
        say("  " + ", ".join("%.1fs %s" % (t, "shown" if sh else "hidden") for t, sh in cursor[-8:]))
        say("  → if it ends **shown** while nothing is printing, that is a program waiting for a key.")
    else:
        say("  → nothing to read here.")
    say()

    say("window title (OSC 0/1/2): %d change%s" % (len(titles), "" if len(titles) == 1 else "s"))
    for t, v in titles[:6]:
        say("    %6.1fs  %r" % (t, v))
    if len(titles) > 6:
        say("    … %d more" % (len(titles) - 6))
    say()

    if spans:
        say("palmar would show WORKING during:")
        for a, b in spans:
            say("    %6.1fs → %6.1fs   (%.1fs)" % (a, b, b - a))
        say()
        say("VERDICT: **the lights work today, with nothing to configure.**")
        say("  This agent animates its window title while it is busy, which is exactly")
        say("  what palmar reads. Nothing to install and no per-agent code.")
    elif titles:
        say("VERDICT: **the lights stay idle.**")
        say("  It sets a title but never animates it, so there is no busy signal to read.")
        say("  palmar needs the output-activity fallback for this one (issue #22).")
    else:
        say("VERDICT: **the lights stay idle.**")
        say("  It never sets a window title at all, so there is nothing to read.")
        say("  palmar needs the output-activity fallback for this one (issue #22).")

    if not spans and marks:
        # Could the fallback actually light this agent up — gauged from the quiet gaps' shape
        gaps = [b - a for a, b in zip(marks, marks[1:]) if b - a > 1.0]
        say()
        say("would the output fallback catch it?")
        say("    %d output burst%s, %d gap%s longer than 1s%s" % (
            len(marks), "" if len(marks) == 1 else "s",
            len(gaps), "" if len(gaps) == 1 else "s",
            (", longest %.1fs" % max(gaps)) if gaps else ""))
        if gaps and len(marks) > 20:
            say("    It prints while it works and goes quiet in between — that is the shape")
            say("    the fallback is built for, so yes.")
        elif len(marks) <= 20:
            say("    It barely prints anything. The fallback may not have enough to go on;")
            say("    this one may need its own integration.")
    say("─" * 72)

    name = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(argv[0]) or "agent")
    out = os.path.abspath("palmar-probe-%s.txt" % name)
    try:
        with open(out, "w") as f:
            f.write("\n".join(lines) + "\n")
        print("", file=sys.stderr)
        print("  이 보고는 파일로도 남았다 — 통째로 보내면 된다:", file=sys.stderr)
        print("     %s" % out, file=sys.stderr)
        print("     cat %s" % out, file=sys.stderr)
    except OSError as e:
        print("\n  (파일로 못 남겼다: %s — 위 블록을 그대로 복사하면 된다)" % e, file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
