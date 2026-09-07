#!/usr/bin/env python3
"""스파이크 F — 재접속 복원: SIGWINCH 흔들기만으로 화면이 되살아나는가 (#18)

**왜 잰다.** 탭을 닫았다 다시 열면 브라우저에는 빈 화면이 온다. 데몬은 살아 있고 PTY 도 살아
있지만, 새 클라이언트에게 무엇을 보낼 것인가. alt-screen 을 쓰는 에이전트는 스크롤백을 재생해도
화면이 안 만들어진다 — 재생하면 그 세션의 모든 다시 그리기가 순서대로 흐를 뿐이다.
가장 싼 길은 크기를 한 칸 바꿨다 되돌려 **앱이 스스로 다시 그리게** 하는 것이다.
이 결과가 ⑦(재시작 전략)의 답을 바꾼다 — 이걸로 충분하면 링버퍼를 넘길 이유가 줄어든다.

**어떻게 잰다.** 화면을 둘 만들어 비교한다(pyte).
  - `full`  : 처음부터 모든 바이트를 먹인 화면 = 사람이 계속 보고 있었다면 봤을 것
  - `fresh` : **재접속 시점 이후 바이트만** 먹인 화면 = 새로 붙은 브라우저가 받는 것
`fresh` 가 `full` 과 같아지면 SIGWINCH 만으로 복원된다는 뜻이다.

무엇을 흔드는가: `TIOCSWINSZ` 로 (rows, cols-1) 을 넣고 잠깐 뒤 (rows, cols) 로 되돌린다.

사용법:
    python3 reconnect.py claude   "prompt 로 쓸 말"
    python3 reconnect.py shell
"""

from __future__ import annotations

import fcntl
import os
import pty
import re
import select
import signal
import struct
import sys
import termios
import time

import pyte

COLS, ROWS = 100, 30
ALT_ON = b"\x1b[?1049h"
ALT_OFF = b"\x1b[?1049l"


def set_winsize(fd: int, rows: int, cols: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def drain(fd: int, seconds: float, sink: bytearray) -> int:
    """seconds 동안 읽을 수 있는 것을 다 읽어 sink 에 넣는다. 읽은 바이트 수를 준다."""
    end = time.time() + seconds
    got = 0
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], max(0.0, end - time.time()))
        if not r:
            continue
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            break
        if not chunk:
            break
        sink += chunk
        got += len(chunk)
    return got


class Screen(pyte.Screen):
    """pyte 가 private DSR(`ESC[?6n` 등)에서 TypeError 를 낸다 — 화면과 무관한 질의라 삼킨다."""

    def report_device_status(self, *args, **kwargs):  # noqa: D102
        return None

    def define_charset(self, *args, **kwargs):
        return None


def render(data: bytes, cols: int = COLS, rows: int = ROWS) -> list[str]:
    screen = Screen(cols, rows)
    stream = pyte.Stream(screen)
    stream.feed(data.decode("utf-8", "replace"))
    return [line.rstrip() for line in screen.display]


def nonblank(lines: list[str]) -> list[str]:
    return [l for l in lines if l.strip()]


def same(a: list[str], b: list[str]) -> bool:
    return nonblank(a) == nonblank(b)


def main() -> None:
    which = sys.argv[1] if len(sys.argv) > 1 else "claude"
    prompt = sys.argv[2] if len(sys.argv) > 2 else "List exactly three fruits, one per line. No other text."

    if which == "shell":
        cmd = [os.environ.get("SHELL", "/bin/zsh"), "-f"]
    else:
        cmd = ["claude"]

    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "CODEX_COMPANION"))}
    env["TERM"] = "xterm-256color"

    pid, master = pty.fork()
    if pid == 0:
        os.execvpe(cmd[0], cmd, env)

    set_winsize(master, ROWS, COLS)
    all_bytes = bytearray()

    print(f"── {' '.join(cmd)}  ({COLS}x{ROWS}) ──")
    drain(master, 6.0, all_bytes)          # 뜨기를 기다린다

    # ── 1. 무언가를 화면에 남긴다 ────────────────────────────
    if which == "shell":
        os.write(master, b"echo MARKER-ONE; echo MARKER-TWO\n")
        drain(master, 2.0, all_bytes)
    else:
        # 뜨자마자 온보딩 대화상자가 있으면 프롬프트가 안 먹는다 — 먼저 치운다.
        for _ in range(3):
            scr = render(bytes(all_bytes))
            if not any(re.search(r"^\s*❯?\s*1\.", l) for l in scr):
                break
            os.write(master, b"\x1b")       # Esc
            drain(master, 1.5, all_bytes)

        os.write(master, prompt.encode())
        time.sleep(0.5)                     # 붙여 쓰면 제출이 안 된다(스파이크 C 교훈)
        os.write(master, b"\r")
        drain(master, 40.0, all_bytes)

    alt = ALT_ON in bytes(all_bytes) and bytes(all_bytes).rfind(ALT_ON) > bytes(all_bytes).rfind(ALT_OFF)
    before = render(bytes(all_bytes))
    got_answer = any(w in "\n".join(before).lower() for w in ("apple", "banana", "cherry", "orange", "grape"))
    print(f"alt-screen: {'예' if alt else '아니오'}   누적 {len(all_bytes)}B   "
          f"답이 화면에: {'예' if got_answer else '아니오 — 대화 없이 잰 셈이다'}")
    print("── 끊기 직전 화면 ──")
    for l in nonblank(before):
        print("  |", l[:96])

    # ── 2. 브라우저가 끊긴다 ─────────────────────────────────
    # 데몬은 계속 살아 있다. 우리는 "새 클라이언트가 받을 바이트" 만 따로 모은다.
    mark = len(all_bytes)
    time.sleep(1.5)
    idle = drain(master, 0.3, all_bytes)    # 끊긴 동안 저절로 온 것

    # ── 3. 다시 붙는다 — 아무것도 재생하지 않고 흔들기만 ────
    t0 = time.time()
    set_winsize(master, ROWS, COLS - 1)
    time.sleep(0.25)
    set_winsize(master, ROWS, COLS)
    got = drain(master, 3.0, all_bytes)
    dt = time.time() - t0

    after_full = render(bytes(all_bytes))
    fresh = render(bytes(all_bytes[mark:]))

    print(f"\n── 흔든 뒤 {dt:.2f}s, 받은 바이트 {got}B (끊긴 동안 {idle}B) ──")
    print("── 새 클라이언트가 그린 화면 (흔든 뒤 바이트만) ──")
    for l in nonblank(fresh):
        print("  |", l[:96])

    ok = same(fresh, after_full)
    print()
    print(f"판정: 새 클라이언트 화면 {'== ' if ok else '!= '}계속 보던 화면  →  "
          f"{'SIGWINCH 만으로 복원된다' if ok else 'SIGWINCH 만으로는 부족하다'}")
    if not ok:
        f, a = nonblank(fresh), nonblank(after_full)
        print(f"  줄 수: 새 {len(f)} vs 원래 {len(a)}")
        for i in range(max(len(f), len(a))):
            x = f[i] if i < len(f) else "(없음)"
            y = a[i] if i < len(a) else "(없음)"
            if x != y:
                print(f"  첫 차이 {i}행:\n    새   |{x[:88]}\n    원래 |{y[:88]}")
                break

    os.kill(pid, signal.SIGHUP)
    os.close(master)


if __name__ == "__main__":
    main()
