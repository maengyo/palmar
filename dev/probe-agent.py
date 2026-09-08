#!/usr/bin/env python3
"""probe-agent — 이 에이전트에서 palmar 의 신호등이 켜질지 **재 본다**.

    python3 dev/probe-agent.py <명령> [인자...]
    예:  python3 dev/probe-agent.py <에이전트 명령>

명령을 진짜 PTY 에 띄우고 **그대로 쓰게 해 준다** — 평소처럼 프롬프트를 넣고, 일을 시키고,
끝나면 그 에이전트를 종료하면 된다. 그동안 palmar 가 볼 것과 **똑같은 바이트**를 기록해 두었다가
나갈 때 보고한다.

왜 필요한가: palmar 는 에이전트에게 아무것도 묻지 않는다. PTY 를 갖고 있으니 흐르는 바이트만
본다(docs/protocol.md "제목으로 읽기"). 그래서 "이 에이전트에서 켜지나?" 는 짐작할 것이 아니라
**한 번 돌려 보면 되는 것**이다.

여기서 재는 것은 palmar 의 판단 근거 그대로다:
  · 창 제목(OSC 0/1/2)이 얼마나 자주 바뀌는가 — 3초 안에 두 번 이상이면 "일하는 중"
  · 조용한 구간이 얼마나 되는가 — 되돌림(출력 활동)이 필요한지 가늠하는 값

이 파일은 개발 도구다. 제품(`palmar/`)에는 안 들어간다.
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

WINDOW_S = 3.0      # palmar/__init__.py 와 같은 창
BUSY_N = 2          # 그 안에 제목이 이만큼 바뀌면 "돌고 있다"
OSC = re.compile(rb"\x1b\][012];([^\x07\x1b]{0,255})(?:\x07|\x1b\\)")

#: 기동할 때 에이전트가 터미널에 던지는 질의. 진짜 터미널은 답한다 — 우리는 그냥 지나보내면 되지만
#: (진짜 터미널이 뒤에 있으니), 무엇을 물었는지는 보고에 적는다.
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
    # 죽이라는 신호에도 **보고는 하고 나간다.** 안 그러면 25초 재 놓고 kill 한 사람은 아무것도 못 본다.
    def _bail(*_):
        raise KeyboardInterrupt
    for _sig in (signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(_sig, _bail)
        except (OSError, ValueError):
            pass

    t0 = time.monotonic()
    titles: list[tuple[float, str]] = []      # (시각, 제목) — 값이 실제로 바뀐 것만
    marks: list[float] = []                   # 바이트가 온 시각
    asked: set[str] = set()
    total = 0
    last_title = None

    watch_stdin = True
    old = None
    try:
        old = termios.tcgetattr(sys.stdin)
        tty.setraw(sys.stdin.fileno())
    except (termios.error, ValueError):
        pass                                   # 파이프로 돌리는 경우 — 기록은 그대로 된다

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
                    # 내 입력이 끝난 것이지 **에이전트가 끝난 것이 아니다.** 여기서 그만두면
                    # 파이프로 돌렸을 때 아무것도 못 재고 0바이트로 끝난다(처음에 그렇게 틀렸다).
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
                os.write(sys.stdout.fileno(), chunk)   # **그대로 보여 준다** — 평소처럼 쓰면 된다
                now = time.monotonic() - t0
                total += len(chunk)
                marks.append(now)
                for q, name in QUERIES:
                    if q in chunk:
                        asked.add(name)
                for m in OSC.finditer(chunk):
                    t = m.group(1).decode("utf-8", "replace")
                    if t != last_title:
                        last_title = t
                        titles.append((now, t))
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

    report(argv, time.monotonic() - t0, total, titles, marks, asked)
    return 0


def report(argv, dur, total, titles, marks, asked) -> None:
    # **파일로도 남긴다.** 전체화면 TUI 가 나가면서 화면을 되돌리면 여기 찍은 것이 묻힌다 —
    # 그러면 다 재 놓고도 무엇을 봐야 할지 모르게 된다(실제로 그랬다).
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

    # 제목이 도는 구간을 palmar 와 같은 규칙으로 센다
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
        # 되돌림이 실제로 이 에이전트를 켤 수 있는지 — 조용한 구간의 모양으로 가늠한다
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
