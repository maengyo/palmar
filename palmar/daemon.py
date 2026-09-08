#!/usr/bin/env python3
"""palmard — palmar 데몬 (#22). 계약은 `docs/protocol.md` 다. 이 파일은 그것만 구현한다.

PTY 를 띄우고, 바이트를 옮기고, 훅을 받고, 폴더를 읽는다. 그 이상은 없다(AGENTS.md 원칙 1).

- 표준 라이브러리만. 파이썬 3.9 문법만 — `/usr/bin/python3` 가 3.9.6 이고 그게 "설치 0" 의 근거다.
- 단일 스레드 asyncio. `pty.fork` 는 스레드가 있으면 경고를 내고 교착 위험이 있다(스파이크 D 조사).
- 웹소켓은 손으로 짠 RFC 6455 — `Frame`/`read_frame` 은 스파이크 D 그대로.
- 흐름 제어 상수(100KB/10KB/256KB)와 병합 시간(5ms)은 스파이크 D 실측값.
- 링버퍼(절대 오프셋 + `since`)는 스파이크 G 의 모양, alt-screen 감지와 SIGWINCH 흔들기는 스파이크 F.
- 캔버스(⑪)와 이름(⑫)은 2026-09-08 에 계약에 붙었다. 데몬이 갖는 것은 id·이름·순서와
  세션의 소속뿐이다 — 미니맵도 목록 접기도 "지금 보고 있는 탭" 도 여기 없다(브라우저만의 것).

    python3 -m palmar            # 127.0.0.1:8801. --port 만 받는다. host 옵션은 없다(#29).

자식 종료 감지는 SIGCHLD → `waitpid(WNOHANG)` 다. kqueue NOTE_EXIT 는 macOS 전용이라 리눅스
폴백이 따로 필요하고, 0.5초 폴링은 유휴를 먹는다(원칙 6). asyncio 가 시그널을 self-pipe 로
루프 안에 넘겨 주므로 단일 스레드가 그대로 유지된다. PTY 가 EOF 를 내는 길도 같은 곳으로 간다.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import collections
import fcntl
import hashlib
import hmac
import json
import os
import pty
import re
import secrets
import signal
import stat
import struct
import sys
import termios
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import PROTOCOL, __version__

if sys.version_info < (3, 9):
    sys.exit("palmard: 파이썬 3.9 이상이 필요하다 (/usr/bin/python3 가 3.9.6 이다)")

WS_MAGIC = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# ── 스파이크 D 실측값. 바꾸려면 protocol.md 를 먼저 고친다 ─────────────────────────────
#: 미확인 바이트가 이만큼 쌓이면 그 pane 의 PTY 읽기를 멈춘다. ttyd 는 100KB x 10, VS Code 는 100KB.
HIGH_WATER = 100_000
#: 여기까지 내려오면 다시 읽는다. VS Code 는 5KB.
LOW_WATER = 10_000
#: 한 번 깨어났을 때 삼킬 최대 바이트. 없으면 끝없이 뱉는 프로그램이 이벤트 루프를 잡는다(polycanv).
PUMP_BUDGET = 256 * 1024
#: PTY 바이트를 이만큼 모았다 보낸다. macOS PTY 는 1KB 씩 읽히므로 안 모으면 프레임이 폭주한다.
COALESCE_MS = 5
#: ⑨ — 링버퍼는 pane 당 256KB.
RING = 256 * 1024
#: 링버퍼 조각 수 상한. alt 를 자주 넘나드는 앱이 조각을 무한히 늘리지 못하게.
RING_MAX_SEGS = 64
#: 스파이크 F — (rows, cols-1) → 50ms → (rows, cols).
SHAKE_MS = 50
#: 브라우저가 보낼 수 있는 웹소켓 메시지 상한. 길이 필드는 2^63 까지 적을 수 있으니 막아야 한다.
MAX_WS_MESSAGE = 16 * 1024 * 1024
#: HTTP 요청 몸 상한.
MAX_BODY = 1024 * 1024
#: pane 입력 큐 상한. PTY 슬레이브가 안 빠질 때 여기까지만 쌓고 넘으면 버린다(로그) (#1).
INPUT_MAX = 1024 * 1024
#: 요청 줄·헤더·몸을 이 안에 못 받으면 닫는다. 업그레이드 뒤 웹소켓 프레임에는 안 걸린다 (#6).
REQUEST_TIMEOUT = 10
#: 세션을 죽일 때 SIGHUP 뒤 이만큼 기다리고 SIGKILL. 오래 도는 데몬에 좀비·유령 셸을 남기지 않는다.
KILL_GRACE_S = 2.0

ALT_ON = b"\x1b[?1049h"
ALT_OFF = b"\x1b[?1049l"

#: 에이전트는 바쁨을 **창 제목**에 싣는다 (#38, 2026-09-08 실측). OSC 0/1/2 = 제목 세우기.
#: codex 는 점자 스피너(⠋⠙⠹…), Claude Code 는 ◐◑ 를 앞에 붙이고 한가해지면 뗀다.
#: **글자표는 만들지 않는다** — 새 에이전트가 나오면 그 자리에서 깨진다. 대신 제목이 계속
#: 바뀌는 것만 본다: 스피너는 정의상 계속 바뀐다. 잰 빈도는 codex ~12회/초, Claude Code ~1회/초라
#: 3초 창이면 둘 다 덮는다. 두 번을 요구하는 것은 한 번짜리 제목 바꾸기(Claude Code 는 턴이
#: 시작될 때 제목을 그 턴의 말로 바꾼다)를 "돌고 있다" 로 읽지 않기 위해서다.
OSC_TITLE = re.compile(rb"\x1b\][012];([^\x07\x1b]{0,255})(?:\x07|\x1b\\)")
TITLE_WINDOW_S = 3.0
TITLE_BUSY_N = 2
OSC_CARRY_MAX = 512          # 종결자 없는 ESC] 가 계속 와도 carry 가 자라지 않게 하는 상한

#: 제목을 아예 안 쓰는 에이전트를 위한 되돌림 (#22). **그런 에이전트가 실제로 있다** — 실측
#: (2026-09-08, `dev/probe-agent.py`): `window title (OSC 0/1/2): 0 changes`. 한 번만 세우고 마는
#: 것도 있다. 어느 것이든 이름으로 알아보지 않는다 — 무엇이 오든 같은 규칙으로 읽는다.
#: 근거 둘을 묶는다:
#:   · `tcgetpgrp(master)` — 앞에서 도는 것이 셸 자신이면 **아무것도 안 돈다**(실측으로 정확히 갈렸다).
#:     이것이 맨 셸을 신호등에서 빼 준다. 없으면 프롬프트만 떠 있는 판이 "끝났다" 로 켜진다.
#:   · 출력 활동 — 도는 것이 있고 최근에 찍었으면 일하는 중, 찍다가 멎었으면 사람을 부른다.
#: **한계는 적어 둔다:** 아무것도 안 찍으면서 오래 생각하는 에이전트는 끝난 것과 구분되지 않는다.
#: 그건 이 근거로는 원리상 알 수 없다 — 훅이나 제목이 있는 에이전트에서 그 둘이 이기는 이유다.
OUT_QUIET_S = 5.0
#: 출력이 **이만큼은 이어져야** 일하는 중으로 친다. 한 번 찍고 마는 것(셸 프롬프트, 끝난 `ls`)은
#: 일이 아니다. 직업 제어가 없는 셸에서는 아래 프롬프트 검사가 무력해지므로 이 값이 그 자리를 맡는다.
OUT_MIN_S = 1.0

#: 폴더 찾기 (`GET /api/dirs?find=`). 위 칸이 "sessions and folders" 를 찾는다고 말하는데,
#: 브라우저는 **이미 펼친 행** 만 걸러 낼 수 있었다 — 펼쳐 본 적 없는 폴더는 안 잡혔다. 약속을 지킨다.
#: 뿌리 아래를 한 번 훑어 이름표를 만들어 두고 그걸 찾는다. 실측(2026-09-08, 진짜 홈):
#:   깊이 2 → 133개 1ms · 깊이 3 → 2,130개 41ms · **깊이 4 → 4,172개 204ms**
#: 204ms 는 **이벤트 루프를 그만큼 막는다** — 단일 스레드라 그동안 모든 판이 멈춘다. 그래서
#: `FIND_YIELD` 개마다 루프에 양보하며 훑는다. 만든 이름표는 `FIND_TTL_S` 동안 다시 쓴다.
#: 있었던 일. **신호등은 "지금" 만 말한다** — 자리를 20분 비웠다 오면 무엇이 끝났고 무엇이 물어봤는지,
#: 어떤 차례로 그랬는지가 아무 데도 안 남아 있었다. 마지막 상태만 있었다.
#: **데몬이 갖는다**: 브라우저를 닫아 두는 동안이야말로 "없는 동안" 이고, 그때 살아 있는 것은 데몬뿐이다.
#: 적는 것은 **놓쳐서 아까운 것**뿐이다 — 나를 부르게 된 순간, 끝난 순간, 생기고 사라진 것.
#: idle→working 같은 것은 안 적는다: 잦고, 놓쳐도 아깝지 않고, 적으면 나머지가 묻힌다.
LOG_MAX = 200

FIND_DEPTH = 4
FIND_YIELD = 400        # 이만큼 훑을 때마다 루프에 양보한다
FIND_TTL_S = 60.0
FIND_MAX = 20000        # 이름표 상한 — 아주 넓은 홈에서 메모리가 자라지 않게
FIND_HITS = 40          # 한 번에 돌려주는 개수
#: 훑지 않는 폴더 이름. 사람이 터미널을 여는 자리가 아니고, 있으면 결과를 통째로 덮는다 —
#: 실측(2026-09-08): macOS 홈에서 "work" 를 찾으니 `~/Library/…/Frameworks` 가 화면을 채웠다
#: (`Frameworks` 안에 work 가 들어 있다). 도구가 만든 나무는 넓기만 하고 갈 일이 없다.
FIND_SKIP = frozenset((
    "Library", "Applications", "node_modules", "__pycache__", "site-packages",
    "venv", ".venv", "dist", "build", "target", "Pods", "DerivedData",
    "vendor", "bower_components", "Caches",
))
#: 번들. **폴더처럼 생겼지만 파일이다** — 여기에 터미널을 열 일은 없고, 안이 아주 넓다
#: (실측: `~/Pictures/Photos Library.photoslibrary` 하나가 결과를 채웠다).
FIND_SKIP_SUFFIX = (".app", ".photoslibrary", ".framework", ".bundle", ".xcodeproj",
                    ".xcworkspace", ".lproj", ".appex", ".sparsebundle", ".fcpbundle")

#: **화면에 아무것도 안 남기는 출력은 일이 아니다.** 어떤 TUI 는 가만히 있어도 커서 관리 시퀀스를
#: 계속 낸다 — 어떤 TUI 는 한가할 때 초당 10번 똑같은 32바이트를 찍었다(실측 2026-09-08, 15초에 147회,
#: 전부 `ESC[?25l ESC[?7l ESC[?7h ESC[0m ESC[?12l ESC[?25h` 하나였고 이스케이프를 걷어낸 내용은 0바이트).
#: 그걸 활동으로 세면 **영원히 working** 이고 done 이 안 온다.
#: **화면을 읽는 것이 아니다** — 무엇이라고 썼는지는 안 본다. 무엇이라도 썼는지만 본다.
ESC_SEQ = re.compile(rb"\x1b(?:\[[0-9;?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[()][A-Za-z0-9]|[@-Z\\-_])")
CONTENT_FAST = 512      # 이보다 크면 따질 것 없이 내용이 있다 — 홍수 때 한 줄 한 줄 훑지 않으려고

# ── 경로 ──────────────────────────────────────────────────────────────────────────
HOME = Path.home().resolve()
PALMAR_DIR = Path.home() / ".palmar"        # shim 의 "$HOME/.palmar" 와 같은 글자여야 한다
BIN_DIR = PALMAR_DIR / "bin"
ZDOT_DIR = PALMAR_DIR / "zsh"      # zsh 래퍼 rc
BASHRC = PALMAR_DIR / "bash" / "bashrc"   # bash 래퍼 rc (--rcfile 로 물린다)      # zsh 를 감싸는 rc. 사용자 rc 뒤에 PATH 를 다시 앞세운다
RUN_DIR = PALMAR_DIR / "run"
TOKEN_FILE = RUN_DIR / "token"
# web/ 는 이 파일 **옆에** 있다. 저장소에서 `python3 -m palmar` 로 돌 때와 휠에서 설치돼 돌 때가
# 같은 경로다 — 둘이 다르면 한쪽에서만 되는 종류의 버그가 생긴다.
WEB = (Path(__file__).resolve().parent / "web").resolve()

PORT = [8801]        # Origin·Host 검사와 훅 URL 에 쓰려고 전역으로 둔다
TOKEN = [""]         # 뜰 때 만든다 — secrets.token_urlsafe(32)
LOCK_FH = [None]     # 단일 인스턴스 락 fd — 데몬이 사는 동안 연 채로 둔다(닫으면 락이 풀린다) (#2)

#: 스파이크 D 의 크기 상한.
MAX_COLS, MAX_ROWS = 500, 200

#: 자식 셸에 물려주지 않는 환경변수 접두. Claude Code 안에서 데몬을 띄우면 자식 claude 가
#: 이걸 물려받아 중첩 세션으로 뜬다(실측, AGENTS.md "검증").
#: **접두는 데몬을 띄운 에이전트의 정체 전부를 덮는다**(2026-09-08 에 넓혔다). 옛 목록
#: ("CLAUDECODE", "CLAUDE_CODE_", "CODEX_COMPANION_") 은 적힌 뜻보다 좁아, 진짜 pane 에 붙어
#: `printenv` 를 돌리니 AI_AGENT·CLAUDE_EFFORT·CLAUDE_PID·CLAUDE_PLUGIN_DATA·CLAUDE_OFFICE_API_URL
#: 다섯이 그대로 넘어가 있었다(실측 2026-09-08, pane 환경변수 55개). 아래 STRIP_ENV_EXACT 가
#: "띄운 터미널의 정체는 물려주지 않는다" 를 하는 것과 같은 자리다 — 다섯 중 비밀을 담은 것은
#: 없었지만(25자 id·"xhigh"·pid·localhost URL·~/.claude/plugins 아래 경로) pane 안의 에이전트가
#: 물려받을 값도 아니다.
#: **안 잰 것:** 저 다섯이 실제로 중첩 claude 의 시작을 바꾸는지는 재지 않았다. 재서가 아니라
#: 규칙("띄운 쪽의 정체를 안 넘긴다")으로 뺀다.
STRIP_ENV_PREFIXES = ("CLAUDECODE", "CLAUDE_", "CODEX_", "AI_AGENT")
#: 접두에 걸리지만 남기는 것 — 사용자가 자기 셸에 직접 두는 설정이라 pane 에서도 그대로여야 한다.
#: 접두를 넓히면서 옛 주석의 예외("사용자가 직접 두는 CLAUDE_CONFIG_DIR 류는 남긴다")를 여기로 옮겼다.
KEEP_ENV_EXACT = ("CLAUDE_CONFIG_DIR",)
#: 데몬을 띄운 터미널의 정체 — pane 에는 틀린 값이다. Terminal.app 에서 띄우면 pane 의 zsh 가
#: /etc/zshrc_Apple_Terminal 을 타서 "Restored session:" 을 찍고, 띄운 터미널과 같은 TERM_SESSION_ID 로
#: ~/.zsh_sessions 히스토리를 공유했다(2026-09-07 통합 실측, 1회). tmux 의 TMUX 도 같은 종류라 함께 뺀다(안 잼).
STRIP_ENV_EXACT = ("TERM_SESSION_ID", "TERM_PROGRAM_VERSION", "ITERM_SESSION_ID", "TMUX", "TMUX_PANE")

#: 훅 이벤트 → status (protocol.md "상태"). None 은 "바꾸지 않음". 화면을 읽어 정하지 않는다.
HOOK_STATUS = {
    "SessionStart": "idle",
    "UserPromptSubmit": "working",
    "PermissionRequest": "waiting",
    "Notification": None,
    "Stop": "done",
    "SessionEnd": "unknown",
}
HOOK_EVENTS = list(HOOK_STATUS)

# protocol.md "shim" 그대로. 데몬이 뜰 때마다 다시 쓴다(0755).
# 자기 디렉터리를 PATH 에서 뺄 때 고정 문자열로 견주고(정규식 아님) 뒤 슬래시 철자도 함께 뺀다(#12):
#   옛 `grep -vx "$d"` 는 $d 를 정규식으로 봐서 `.palmar` 의 `.` 가 아무 글자나 맞았고(예: /Xpalmar/bin),
#   `/.palmar/bin/` 처럼 뒤 슬래시가 붙은 철자는 못 빼 `command -v claude` 가 자기(shim)를 다시 골라 무한 exec 했다.
# ── zsh 래퍼 ────────────────────────────────────────────────────────────────────
# PATH 를 앞세우는 것만으로는 진다 — 사용자 rc 가 나중에 돌며 자기 것을 다시 앞에 붙인다.
# ZDOTDIR 을 우리 것으로 바꾸고, 우리 rc 가 사용자 rc 를 부른 **뒤에** PATH 를 다시 앞세운다.
# 사용자 파일은 읽기만 한다. zsh 는 ZDOTDIR 의 .zshenv → .zprofile → .zshrc → .zlogin 을 본다.
ZSHENV = """# palmar. 사용자 것을 먼저 부른다.
[ -r "${PALMAR_USER_ZDOTDIR:-$HOME}/.zshenv" ] && . "${PALMAR_USER_ZDOTDIR:-$HOME}/.zshenv"
"""

ZPROFILE = """# palmar.
[ -r "${PALMAR_USER_ZDOTDIR:-$HOME}/.zprofile" ] && . "${PALMAR_USER_ZDOTDIR:-$HOME}/.zprofile"
"""

ZLOGIN = """# palmar.
[ -r "${PALMAR_USER_ZDOTDIR:-$HOME}/.zlogin" ] && . "${PALMAR_USER_ZDOTDIR:-$HOME}/.zlogin"
"""

ZSHRC = """# palmar 가 만든 것. 고치지 마라 — 데몬이 뜰 때마다 다시 쓴다.
# 사용자 rc 를 먼저 부르고, 그 뒤에 shim 을 PATH 앞에 되돌린다.
[ -r "${PALMAR_USER_ZDOTDIR:-$HOME}/.zshrc" ] && . "${PALMAR_USER_ZDOTDIR:-$HOME}/.zshrc"
case ":$PATH:" in
  ":$HOME/.palmar/bin:"*) ;;                       # 이미 맨 앞이면 그대로
  *) PATH="$HOME/.palmar/bin:$PATH"; export PATH ;;
esac
# 사용자가 rc 안에서 ZDOTDIR 을 자기 홈으로 되돌렸을 수 있다 — 그건 그대로 둔다.
# 이 파일은 이미 다 돌았고, 다음 셸은 palmar 가 다시 환경을 준다.
"""

BASH_RC = """# palmar 가 만든 것. 고치지 마라 — 데몬이 뜰 때마다 다시 쓴다.
# bash 에는 ZDOTDIR 이 없어 --rcfile 로 물린다. 사용자 것을 먼저 부르고 PATH 를 되돌린다.
if [ -n "$PALMAR_USER_BASH_PROFILE" ] && [ -r "$PALMAR_USER_BASH_PROFILE" ] && shopt -q login_shell; then
  . "$PALMAR_USER_BASH_PROFILE"
elif [ -n "$PALMAR_USER_BASHRC" ] && [ -r "$PALMAR_USER_BASHRC" ]; then
  . "$PALMAR_USER_BASHRC"
fi
case ":$PATH:" in
  ":$HOME/.palmar/bin:"*) ;;
  *) PATH="$HOME/.palmar/bin:$PATH"; export PATH ;;
esac
"""

SHIM = """#!/bin/sh
# palmar shim: attaches hooks to a claude started inside a palmar terminal. Nothing else.
d=$(cd "$(dirname "$0")" && pwd)
new=
IFS=:
for e in $PATH; do
  case "${e%/}" in
    "$d") ;;                       # 자기 디렉터리 — 뒤 슬래시 있든 없든 뺀다 (고정 문자열 비교)
    *) new="${new:+$new:}$e" ;;
  esac
done
unset IFS
PATH=$new
real=$(command -v claude) || { echo "palmar: claude not found on PATH" >&2; exit 127; }
f="$HOME/.palmar/run/$PALMAR_PANE.json"
[ -n "$PALMAR_PANE" ] && [ -r "$f" ] && exec "$real" --settings "$f" "$@"
exec "$real" "$@"
"""

# palmar/web/index.html 이 아직 없을 때 GET / 가 그래도 200 과 토큰을 돌려주도록 하는 자리표.
# web/ 은 다른 사람이 쓰고 있다 — 여기서 만들지 않는다.
PLACEHOLDER_INDEX = b"""<!doctype html>
<html><head><meta charset="utf-8"><title>palmar</title></head>
<body style="font-family:system-ui,sans-serif;margin:2rem;max-width:40rem">
<h1>palmar</h1>
<p>The daemon is running, but <code>palmar/web/index.html</code> is not there yet.</p>
<p>The API is up: <code>GET /api/sessions</code>, <code>POST /api/sessions</code>,
<code>PATCH /api/sessions/&lt;id&gt;</code>, <code>/api/canvases</code>,
<code>GET /api/dirs</code>, <code>ws /events</code>, <code>ws /pty/&lt;id&gt;</code>.
See <code>docs/protocol.md</code>.</p>
</body></html>
"""

CONTENT_TYPES = {
    ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css",
    ".json": "application/json", ".map": "application/json", ".svg": "image/svg+xml",
    ".png": "image/png", ".ico": "image/x-icon", ".woff2": "font/woff2", ".woff": "font/woff",
    ".wasm": "application/wasm", ".txt": "text/plain",
}

REASONS = {
    200: "OK", 201: "Created", 204: "No Content", 400: "Bad Request", 403: "Forbidden",
    404: "Not Found", 405: "Method Not Allowed", 408: "Request Timeout", 409: "Conflict",
    413: "Payload Too Large", 500: "Internal Server Error",
}


def log(*a) -> None:
    print(time.strftime("%H:%M:%S"), *a, file=sys.stderr, flush=True)


def set_winsize(fd: int, rows: int, cols: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def clamp_int(raw, default: int, lo: int, hi: int) -> int:
    try:
        v = int(raw)
    except (TypeError, ValueError, OverflowError):   # int(float('inf')) 는 OverflowError (#3)
        return default
    return min(hi, max(lo, v))


#: 이름 길이 상한 (protocol.md "캔버스"). **잰 값이 아니라 골라 잡은 값이다** — 데몬이 오래 도니(⑦=b)
#: 길이 없는 문자열을 받아 두지 않으려는 것뿐이고, 한 곳에 있어 바꾸기 싸다.
NAME_MAX = 64


def clean_name(raw):
    """이름 규칙(캔버스·세션 공통, protocol.md "캔버스"). 돌려주는 것이 저장할 값이다.

    `null`·빈 문자열·공백뿐인 문자열은 **이름 없음**으로 같게 다뤄 None 을 돌려준다
    (= 이름 지우기는 {"name": null} 이나 {"name": ""} 둘 다 된다).
    문자열이면 앞뒤 공백을 떼고 1-64 글자, 제어문자(\x00-\x1f, \x7f) 금지. 어기면 ValueError.
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError("name must be a string or null")
    s = raw.strip()
    if not s:
        return None
    if len(s) > NAME_MAX:
        raise ValueError("name must be 64 characters or fewer")
    if any(ch < "\x20" or ch == "\x7f" for ch in s):
        raise ValueError("name must not contain control characters")
    return s


def qget(q: dict, name: str, default=None):
    v = q.get(name)
    return v[0] if v else default


# ── 웹소켓 프레이밍 (스파이크 D) ──────────────────────────────────────────────────────
class Frame:
    """RFC 6455 프레이밍. 서버는 마스킹하지 않고, 클라이언트 것은 언마스킹한다."""

    @staticmethod
    def build(payload: bytes, opcode: int = 0x2) -> bytes:
        n = len(payload)
        if n < 126:
            head = struct.pack("!BB", 0x80 | opcode, n)
        elif n < 65536:
            head = struct.pack("!BBH", 0x80 | opcode, 126, n)
        else:
            head = struct.pack("!BBQ", 0x80 | opcode, 127, n)
        return head + payload

    @staticmethod
    def text(obj) -> bytes:
        return Frame.build(json.dumps(obj, separators=(",", ":")).encode(), 0x1)

    @staticmethod
    def close(code: int = 1000) -> bytes:
        return Frame.build(struct.pack("!H", code), 0x8)


async def read_frame(reader) -> tuple[bool, int, bytes]:
    """프레임 하나. (fin, opcode, payload). 스파이크 D 에 길이 상한만 더했다."""
    head = await reader.readexactly(2)
    fin = bool(head[0] & 0x80)
    opcode = head[0] & 0x0F
    masked = head[1] & 0x80
    n = head[1] & 0x7F
    if n == 126:
        n = struct.unpack("!H", await reader.readexactly(2))[0]
    elif n == 127:
        n = struct.unpack("!Q", await reader.readexactly(8))[0]
    if n > MAX_WS_MESSAGE:
        raise ValueError("websocket frame too large")
    mask = await reader.readexactly(4) if masked else b""
    payload = await reader.readexactly(n) if n else b""
    if masked and n:
        # 정수 XOR — 바이트별 파이썬 루프는 16MB 프레임에 1.17초로 이벤트 루프(모든 pane)를 잡는다 (#9).
        full = (mask * (n // 4 + 1))[:n]
        payload = (int.from_bytes(payload, "big") ^ int.from_bytes(full, "big")).to_bytes(n, "big")
    return fin, opcode, payload


async def read_message(reader, writer) -> tuple[int, bytes] | None:
    """프레임을 메시지로 모은다. 조각(FIN=0)은 이어 붙이고, ping 에는 pong 을 돌려준다.
    (opcode, payload). close 프레임이면 None."""
    op = None
    buf = bytearray()
    while True:
        fin, opcode, payload = await read_frame(reader)
        if opcode == 0x8:
            return None
        if opcode == 0x9:
            writer.write(Frame.build(payload, 0xA))
            continue
        if opcode == 0xA:
            continue
        if opcode == 0x0:
            if op is None:
                raise ValueError("continuation frame without a start")
        else:
            op = opcode
            buf = bytearray()
        buf += payload
        if len(buf) > MAX_WS_MESSAGE:
            raise ValueError("websocket message too large")
        if fin:
            return op, bytes(buf)


# ── 링버퍼 (스파이크 G 의 Pane.since 를 절대 오프셋 조각으로 일반화) ───────────────────────
class Ring:
    """절대 오프셋이 붙은 바이트 조각들, 합쳐서 `cap` 바이트까지.

    스파이크 G 는 `produced - len(ring)` 이 첫 바이트의 오프셋이라는 전제로 `since` 를 풀었다.
    제품에서는 alt-screen 구간의 바이트가 클라이언트에는 나가되(오프셋은 오른다) 링에는 안
    들어가므로(⑨ "alt 안에서는 버퍼를 키우지 않는다"), 링이 오프셋과 연속이 아닐 수 있다.
    그래서 조각마다 시작 오프셋을 든다. alt 를 안 쓰는 셸이면 조각은 늘 하나다.
    """

    def __init__(self, cap: int):
        self.cap = cap
        self.segs: collections.deque = collections.deque()   # [start_offset, bytearray]
        self.size = 0

    def append(self, start: int, data: bytes) -> None:
        if not data:
            return
        if self.segs and self.segs[-1][0] + len(self.segs[-1][1]) == start:
            self.segs[-1][1] += data
        else:
            self.segs.append([start, bytearray(data)])
        self.size += len(data)
        while self.size > self.cap:
            over = self.size - self.cap
            first = self.segs[0]
            if len(first[1]) <= over:
                self.size -= len(first[1])
                self.segs.popleft()
            else:
                del first[1][:over]
                first[0] += over
                self.size -= over
        while len(self.segs) > RING_MAX_SEGS:
            self.size -= len(self.segs[0][1])
            self.segs.popleft()

    def since(self, offset: int) -> bytes:
        """offset 바이트째부터 링에 있는 것. 밀려났으면 있는 데부터(스파이크 G)."""
        out = bytearray()
        for start, buf in self.segs:
            if start + len(buf) <= offset:
                continue
            out += buf[max(0, offset - start):]
        return bytes(out)


# ── 세션 = pane = PTY 하나 ──────────────────────────────────────────────────────────
class Attach:
    """pane 채널 하나 = 브라우저 하나. 미확인 바이트는 각자 센다(흐름 제어는 가장 뒤처진 것 기준)."""
    __slots__ = ("writer", "unacked")

    def __init__(self, writer):
        self.writer = writer
        self.unacked = 0


class Session:
    """PTY 하나 + 링버퍼 + 붙어 있는 브라우저들. 브라우저가 없어도 산다(원칙 2)."""

    def __init__(self, sid: str, cwd: str, cols: int, rows: int, canvas: str, name=None):
        self.id = sid
        self.cwd = cwd
        self.cols, self.rows = cols, rows
        self.canvas = canvas    # 붙어 있는 캔버스 id. **null 이 아니다** — 세션은 늘 어딘가에 있다 (⑪)
        self.name = name        # 사람이 준 이름. None 이면 브라우저가 경로로 이름표를 만든다 (⑫)
        self.status = "unknown"
        self.agent = None
        self.last_event = None
        self.alt = False
        self.created = time.time()
        self.title = ""              # 에이전트가 마지막으로 세운 창 제목 (#38)
        self.title_hits = []         # 최근 제목 변경 시각(monotonic). TITLE_WINDOW_S 밖은 버린다
        self.title_timer = None
        self.osc_carry = b""         # 조각 경계에 걸린 OSC 후보
        self.derived = "idle"        # 훅이 없을 때 제목·출력으로 읽은 상태
        #: 이 판의 제목이 **실제로 돈 적이 있나**. "한 번이라도 세웠나" 가 아니다 —
        #: 셸이 프롬프트마다 제목을 세우는 것은 아주 흔하고(WSL 의 bash 기본값이 그렇다:
        #: `\e]0;\u@\h: \w\a`), 그것을 "이 판은 제목으로 읽는다" 로 받으면 **되돌림이 통째로
        #: 꺼진다.** 셸 제목은 돌지 않으므로 신호등은 영영 idle 이다(사용자 보고 2026-09-08:
        #: title=`linux user@…` 인데 status=idle. macOS 의 맨 zsh 는 제목을 안 세워서 안 보였다).
        self.title_spun = False
        self.logged = "unknown"      # 마지막으로 있었던 일에 적은 상태 (registry.changed 가 본다)
        self.last_out = 0.0          # 마지막으로 바이트가 나온 시각(monotonic)
        self.out_start = 0.0         # 지금 이어지는 출력 묶음이 시작된 시각
        self.out_timer = None
        #: 앞 프로세스 그룹이 셸과 **달랐던 적이 있나**. 직업 제어가 없는 셸(`/bin/sh` 비대화형)에서는
        #: 자식이 셸과 같은 그룹에 있어 `tcgetpgrp` 이 영영 셸을 가리킨다 — 그것을 "아무것도 안 돈다"
        #: 로 읽으면 **에이전트가 내내 찍고 있어도 idle** 이다(실측 2026-09-08: SHELL=/bin/sh 로 14초 내내).
        #: 그래서 이 신호는 **한 번이라도 달라진 것을 본 뒤에만** 믿는다.
        self.fg_varied = False

        self.ring = Ring(RING)
        self.produced = 0            # 지금까지 클라이언트에 나간 총 바이트 (절대 오프셋)
        self.carry = b""             # 조각 경계에 걸린 ESC[?1049 접두 후보 — 다음 flush 에서 판정한다
        self.pending = bytearray()
        self.flush_handle = None
        self.reading = False
        self.inq = bytearray()       # PTY 로 아직 다 못 쓴 입력. 슬레이브 큐가 차면 add_writer 로 뒤를 쓴다 (#1)
        self.writing = False
        self.closed = False
        self.attached: list[Attach] = []
        self.settings_path = RUN_DIR / f"{sid}.json"

        self.pid, self.master = self._spawn()
        set_winsize(self.master, rows, cols)
        os.set_blocking(self.master, False)

    def _spawn(self) -> tuple[int, int]:
        # 데몬이 $SHELL 을 박아 쓴다. 클라이언트가 명령을 고를 길은 없다(#29, 원칙 4).
        shell = os.environ.get("SHELL") or "/bin/sh"
        env = {k: v for k, v in os.environ.items()
               if k in KEEP_ENV_EXACT
               or (not k.startswith(STRIP_ENV_PREFIXES) and k not in STRIP_ENV_EXACT)}
        env["PATH"] = f"{BIN_DIR}:{env.get('PATH', '/usr/bin:/bin')}"   # shim 이 맨 앞
        # PATH 를 앞세우는 것만으로는 진다 — 사용자 rc 가 나중에 실행돼 자기 것을 다시 앞에 붙인다
        # (실측: ~/.zshrc 의 `export PATH="$HOME/.local/bin:$PATH"` 한 줄에 shim 이 밀렸다).
        # zsh 는 ZDOTDIR 로 감싸 우리 rc 가 **마지막에** 돌게 한다. 사용자 파일은 안 건드린다.
        base = os.path.basename(shell)
        argv = [shell]
        if base == "zsh" and (ZDOT_DIR / ".zshrc").exists():
            env["PALMAR_USER_ZDOTDIR"] = env.get("ZDOTDIR") or str(HOME)
            env["ZDOTDIR"] = str(ZDOT_DIR)
        elif base == "bash" and BASHRC.exists():
            # bash 에는 ZDOTDIR 이 없다. --rcfile 이 대화형 셸의 rc 를 갈아끼운다 —
            # 우리 것이 사용자 것을 먼저 부르고 그 뒤에 PATH 를 되돌린다(실측 2026-09-08).
            # 로그인 셸(-l)은 .bash_profile 을 보므로 이 수가 안 먹는다 — 아래 래퍼가 그것도 부른다.
            env["PALMAR_USER_BASHRC"] = str(HOME / ".bashrc")
            env["PALMAR_USER_BASH_PROFILE"] = str(HOME / ".bash_profile")
            argv = [shell, "--rcfile", str(BASHRC)]
        env["PALMAR_PANE"] = self.id
        env["TERM"] = "xterm-256color"
        env["TERM_PROGRAM"] = "palmar"     # tmux 가 TERM_PROGRAM=tmux 를 두는 것과 같은 자리. 띄운 터미널 이름을 덮는다
        pid, master = pty.fork()
        if pid == 0:  # 자식 — 여기서 돌아오지 않는다. 예외를 부모 쪽 asyncio 로 흘리면 안 된다.
            try:
                os.chdir(self.cwd)
            except OSError:
                try:
                    os.chdir(str(HOME))
                except OSError:
                    pass
            try:
                os.execvpe(shell, argv, env)
            except OSError:
                os.write(2, f"palmar: cannot exec {shell}\n".encode())
            os._exit(127)
        return pid, master

    def to_json(self) -> dict:
        return {
            "id": self.id, "cwd": self.cwd, "cols": self.cols, "rows": self.rows,
            "status": self.eff_status(), "agent": self.agent, "alt": self.alt,
            "title": self.title or None,
            "created": self.created, "last_event": self.last_event,
            "canvas": self.canvas, "name": self.name,
        }

    # ── PTY → 브라우저 (스파이크 D) ──────────────────────────────
    def start_reading(self) -> None:
        if self.reading or self.closed:
            return
        asyncio.get_running_loop().add_reader(self.master, self._on_readable)
        self.reading = True

    def stop_reading(self) -> None:
        if not self.reading:
            return
        asyncio.get_running_loop().remove_reader(self.master)
        self.reading = False

    def _backpressure(self) -> int:
        """가장 뒤처진 브라우저의 미확인 + 아직 안 보낸 pending. 붙은 게 없으면 0 — 링버퍼가 받으니
        계속 읽는다(protocol.md '아무도 없으면')."""
        if not self.attached:
            return 0
        return max(a.unacked for a in self.attached) + len(self.pending)

    def _on_readable(self) -> None:
        got = 0
        while got < PUMP_BUDGET:
            if self._backpressure() >= HIGH_WATER:   # 안 보낸 pending 까지 세어 상한을 실제로 지킨다 (#5)
                break
            try:
                chunk = os.read(self.master, 65536)
            except BlockingIOError:
                break
            except OSError:
                # 리눅스는 자식이 죽으면 EIO, macOS 는 빈 바이트다(조사).
                self.die("pty closed")
                return
            if not chunk:
                self.die("pty eof")
                return
            self.pending += chunk
            got += len(chunk)
        if self.pending and self.flush_handle is None:
            self.flush_handle = asyncio.get_running_loop().call_later(COALESCE_MS / 1000, self._flush)
        self._flow()

    def _flush(self) -> None:
        self.flush_handle = None
        if not self.pending or self.closed:
            return
        data = bytes(self.pending)
        self.pending.clear()
        self._emit(data)

    def _emit(self, data: bytes) -> None:
        """링버퍼에 넣고(alt 구간은 빼고) 붙어 있는 모두에게 한 프레임으로 보낸다."""
        self._scan_title(data)          # 제목은 **바이트를 건드리지 않고** 지켜보기만 한다 (#38)
        self._out_scan(data)            # 제목을 안 쓰는 에이전트는 출력으로 읽는다 (#22)
        alt_changed = self._absorb(data)
        frame = Frame.build(data)
        for a in list(self.attached):
            try:
                a.writer.write(frame)
                a.unacked += len(data)
            except Exception:
                self.detach(a)
        self._flow()
        if alt_changed:
            registry.changed(self)   # alt 가 바뀌면 /events 로 session 을 보낸다

    # ── 제목으로 상태 읽기 (#38) ─────────────────────────
    def _scan_title(self, data: bytes) -> None:
        """제목 시퀀스를 세기만 한다. 링에서 빼지도, 브라우저로 가는 프레임에서 빼지도 않는다 —
        xterm 이 그대로 받아 제 일을 해야 한다(alt 마커와 다른 점이다)."""
        buf = self.osc_carry + data
        last = 0
        hit = False
        for m in OSC_TITLE.finditer(buf):
            last = m.end()
            t = m.group(1).decode("utf-8", "replace")
            if t != self.title:        # 같은 제목을 다시 세우는 것은 변화가 아니다
                self.title = t
                self.title_hits.append(time.monotonic())
                hit = True
        rest = buf[last:]
        i = rest.rfind(b"\x1b]")
        # 하이퍼링크(OSC 8) 처럼 여기 규칙에 안 걸리는 OSC 도 여기 남는다 — 상한이 그것을 받아 낸다.
        self.osc_carry = rest[i:][-OSC_CARRY_MAX:] if i >= 0 else b""
        if hit:
            self._title_tick()
            self._arm_settle()

    def _arm_settle(self) -> None:
        """제목이 멎는 순간에는 바이트가 안 온다 — 멎었는지는 시계로만 알 수 있다.
        **마지막 변경에 맞춰** 깨운다. 고정 간격으로 깨우면 실제로 멎은 시각과 그것을 알아채는
        시각이 최대 한 주기만큼 벌어진다(실측: codex 가 3.4초에 한가해졌는데 9.4초에 알았다)."""
        if self.title_timer is not None:
            self.title_timer.cancel()
        last = self.title_hits[-1] if self.title_hits else time.monotonic()
        delay = max(0.05, TITLE_WINDOW_S - (time.monotonic() - last) + 0.05)
        self.title_timer = asyncio.get_running_loop().call_later(delay, self._title_settle)

    def _title_busy(self) -> bool:
        now = time.monotonic()
        self.title_hits = [t for t in self.title_hits if now - t <= TITLE_WINDOW_S]
        return len(self.title_hits) >= TITLE_BUSY_N

    def _title_tick(self) -> None:
        """돌고 있으면 working, **돌다가** 멎으면 done. 돌지도 않았는데 done 이 되지는 않는다 —
        가만히 떠 있는 TUI(vim 같은 것)를 '끝났다' 로 만들면 신호등이 늘 켜져 있게 된다."""
        busy = self._title_busy()
        if busy:
            self.title_spun = True         # **여기서만** 제목 층이 주도권을 갖는다
        elif not self.title_spun:
            return                         # 아직 돈 적 없다 — 판단은 되돌림에 맡긴다
        want = "working" if busy else ("done" if self.derived == "working" else self.derived)
        if want != self.derived:
            self.derived = want
            if self.status == "unknown":      # 훅이 말해 주는 세션이면 화면을 흔들지 않는다
                registry.changed(self)

    def _title_settle(self) -> None:
        self.title_timer = None
        if self.closed:
            return
        if self._title_busy():
            self._arm_settle()
        else:
            self._title_tick()

    # ── 출력으로 상태 읽기 — 제목을 안 쓰는 에이전트용 되돌림 (#22) ──────────
    @staticmethod
    def _has_content(data: bytes) -> bool:
        """이 조각이 화면에 무언가를 남기나. 커서만 움직이는 것은 아니다."""
        if len(data) > CONTENT_FAST:
            return True
        rest = ESC_SEQ.sub(b"", data)
        return any(b >= 0x20 or b in (0x07, 0x09, 0x0a, 0x0d) for b in rest)

    def _out_scan(self, data: bytes) -> None:
        """바이트가 나올 때마다 부른다. 제목을 쓰는 세션에서는 아무 일도 안 한다 —
        제목이 더 정확하고, 둘이 같은 값을 놓고 다투면 신호등이 떤다."""
        if not self._has_content(data):
            return                       # 커서 관리 틱 — 안 온 것으로 친다
        now = time.monotonic()
        if now - self.last_out > OUT_QUIET_S:
            self.out_start = now         # 조용하다가 다시 찍기 시작했다 — 새 묶음
        self.last_out = now
        if self.title_spun:
            return
        self._out_tick()
        self._arm_out()

    def _arm_out(self) -> None:
        """멎는 순간에는 바이트가 안 온다 — 제목 쪽과 같은 이유로 시계가 필요하다."""
        if self.out_timer is not None:
            self.out_timer.cancel()
        delay = max(0.05, OUT_QUIET_S - (time.monotonic() - self.last_out) + 0.05)
        self.out_timer = asyncio.get_running_loop().call_later(delay, self._out_settle)

    def _out_settle(self) -> None:
        self.out_timer = None
        if self.closed or self.title_spun:
            return
        if time.monotonic() - self.last_out < OUT_QUIET_S:
            self._arm_out()                 # 그 사이 또 찍었다
        else:
            self._out_tick()

    def _at_prompt(self) -> bool:
        """앞에서 도는 프로세스 그룹이 셸 자신인가 = 아무것도 안 돈다.
        `pty.fork` 가 자식을 세션 리더로 만들므로 셸의 pgid 는 곧 그 pid 다.

        **한 번이라도 달라진 것을 본 뒤에만 믿는다.** 직업 제어가 없는 셸에서는 이 값이 영영
        셸을 가리키는데, 그것은 "아무것도 안 돈다" 가 아니라 **알 수 없다** 는 뜻이다."""
        try:
            fg = os.tcgetpgrp(self.master)
        except OSError:
            return False                    # 못 물어보면 모르는 것이지 프롬프트인 것이 아니다
        if fg != self.pid:
            self.fg_varied = True
            return False
        return self.fg_varied

    def _out_tick(self) -> None:
        """프롬프트면 idle. 뭔가 돌고 **이어서** 찍고 있으면 working. 찍다가 멎었으면 done —
        찍은 적도 없는데 done 이 되지는 않는다(제목 쪽과 같은 규율)."""
        now = time.monotonic()
        if self._at_prompt():
            want = "idle"
        elif now - self.last_out < OUT_QUIET_S and self.last_out - self.out_start >= OUT_MIN_S:
            want = "working"
        elif now - self.last_out < OUT_QUIET_S:
            want = self.derived             # 한 번 찍고 만 것 — 아직 일이라고 부르지 않는다
        else:
            want = "done" if self.derived == "working" else self.derived
        if want != self.derived:
            self.derived = want
            if self.status == "unknown":
                registry.changed(self)

    def eff_status(self) -> str:
        """훅이 말해 주면 그 말을 쓴다 — 훅은 정확하고 제목은 짐작이다. 훅이 없는 에이전트만
        제목으로 읽는다(회사에서 쓰는 codex 처럼 훅이 안 열려 있을 수 있다 — #15)."""
        return self.status if self.status != "unknown" else self.derived

    def _absorb(self, data: bytes) -> bool:
        """스파이크 F 의 alt 감지: ESC[?1049h → alt, ESC[?1049l → 해제. 마커 자체와 alt 안의 바이트는
        링에 넣지 않는다 — 들어가기 전 바이트는 남는다. 조각 경계에 걸린 접두는 carry 로 넘긴다.
        alt 상태가 바뀌었으면 True."""
        changed = False
        buf = self.carry + data
        base = self.produced - len(self.carry)     # buf[0] 의 절대 오프셋
        pos = 0
        while True:
            i_on = buf.find(ALT_ON, pos)
            i_off = buf.find(ALT_OFF, pos)
            hits = [i for i in (i_on, i_off) if i >= 0]
            if not hits:
                break
            i = min(hits)
            if not self.alt:
                self.ring.append(base + pos, buf[pos:i])
            new_alt = i == i_on
            if new_alt != self.alt:
                self.alt = new_alt
                changed = True
            pos = i + len(ALT_ON)
        tail = buf[pos:]
        hold = 0
        for n in range(min(len(ALT_ON) - 1, len(tail)), 0, -1):
            if ALT_ON.startswith(tail[-n:]):       # ALT_OFF 와 앞 7바이트가 같다
                hold = n
                break
        body = tail[:len(tail) - hold] if hold else tail
        if not self.alt:
            self.ring.append(base + pos, body)
        self.carry = tail[len(tail) - hold:] if hold else b""
        self.produced += len(data)
        return changed

    def _flow(self) -> None:
        """미확인(+안 보낸 pending) 바이트가 HIGH 이상이면 읽기를 멈추고 LOW 이하로 내려오면 다시 읽는다.
        브라우저가 여럿이면 가장 뒤처진 것 기준. 아무도 없으면 계속 읽는다 — 링버퍼가 받는다."""
        if self.closed:
            return
        worst = self._backpressure()
        if self.reading and worst >= HIGH_WATER:
            self.stop_reading()
        elif not self.reading and worst <= LOW_WATER:
            self.start_reading()

    def ack(self, a: Attach, n: int) -> None:
        a.unacked = max(0, a.unacked - max(0, n))
        self._flow()

    # ── 붙기·떼기 (protocol.md "pane 채널") ─────────────────────────
    def attach(self, writer, cols: int | None, rows: int | None, frm: int) -> Attach:
        a = Attach(writer)
        frm = min(max(0, frm), self.produced)
        replay = b""
        if not self.alt:
            replay = self.ring.since(frm)
            if self.carry and frm < self.produced:
                # carry 는 이미 나간 바이트인데 링에는 아직 안 들어갔다 — 재생에 붙인다.
                carry_start = self.produced - len(self.carry)
                replay += self.carry[max(0, frm - carry_start):]
        hello = {"t": "hello", "offset": self.produced, "alt": self.alt, "replayed": len(replay)}
        writer.write(Frame.text(hello))
        if replay:
            writer.write(Frame.build(replay))
            a.unacked += len(replay)          # 브라우저는 이것도 ack 한다
        self.attached.append(a)
        if self.alt:
            # 재생하지 않고 흔든다(스파이크 F). 붙을 때 준 크기가 다르면 그것이 곧 진짜 리사이즈다.
            self.shake(cols or self.cols, rows or self.rows)
        elif cols and rows:
            self.resize(cols, rows)
        self._flow()
        return a

    def detach(self, a: Attach) -> None:
        if a in self.attached:
            self.attached.remove(a)
        try:
            a.writer.close()
        except Exception:
            pass
        self._flow()

    # ── 브라우저 → PTY ────────────────────────────────
    def send_input(self, data: bytes) -> None:
        """PTY master 를 backpressure 있는 스트림으로 다룬다. os.write 는 짧게 쓸 수 있다 —
        master 는 논블로킹이고 macOS 는 슬레이브 입력 큐(TTYHOG≈1024)가 차면 부분 카운트를 돌려준다
        (#1 실측: 1024/8000/70000 바이트가 다 1022 로 잘렸다). 반환값을 버리면 1KB 넘는 붙여 넣기가 소리
        없이 잘린다. 남은 것은 inq 에 두고 슬레이브가 빠져 fd 가 다시 쓸 수 있을 때 마저 쓴다."""
        if self.closed or not data:
            return
        space = INPUT_MAX - len(self.inq)
        if space <= 0:
            log(f"session {self.id}: input queue full, dropping {len(data)} bytes")
            return
        if len(data) > space:
            log(f"session {self.id}: input queue near full, dropping {len(data) - space} bytes")
            data = data[:space]
        self.inq += data
        self._pump_input()

    def _pump_input(self) -> None:
        """inq 를 쓸 수 있는 만큼 쓰고, 남으면 add_writer 로 다음 기회를 기다린다. add_writer 콜백도 이걸 부른다."""
        if self.closed:
            return
        if self.inq:
            try:
                n = os.write(self.master, self.inq)
            except BlockingIOError:
                n = 0
            except OSError:
                self.die("pty write failed")
                return
            if n:
                del self.inq[:n]
        loop = asyncio.get_running_loop()
        if self.inq and not self.writing:
            loop.add_writer(self.master, self._pump_input)
            self.writing = True
        elif not self.inq and self.writing:
            loop.remove_writer(self.master)
            self.writing = False

    def resize(self, cols: int, rows: int, force: bool = False) -> None:
        if self.closed:
            return
        changed = (cols, rows) != (self.cols, self.rows)
        if not (changed or force):
            return
        try:
            set_winsize(self.master, rows, cols)
        except OSError:
            return
        self.cols, self.rows = cols, rows
        if changed:
            registry.changed(self)

    def shake(self, cols: int, rows: int) -> None:
        """SIGWINCH 흔들기: (rows, cols-1) → 50ms → (rows, cols). alt-screen 앱이 스스로 다시 그린다."""
        if self.closed:
            return
        try:
            set_winsize(self.master, rows, max(1, cols - 1))
        except OSError:
            return
        asyncio.get_running_loop().call_later(SHAKE_MS / 1000, self.resize, cols, rows, True)

    # ── 훅 (protocol.md "상태") ─────────────────────────
    def on_hook(self, agent: str, payload) -> None:
        ev = payload.get("hook_event_name") if isinstance(payload, dict) else None
        if not isinstance(ev, str):
            return
        before = (self.status, self.agent, self.last_event)
        self.last_event = ev
        if ev == "SessionEnd":
            self.status = "unknown"
            self.agent = None
        else:
            new = HOOK_STATUS.get(ev)      # Notification 과 모르는 이벤트는 바꾸지 않는다
            if new:
                self.status = new
            # 이 길로 온 훅은 그 에이전트 것이다. SessionStart 의 http 훅이 안 온 관측(스파이크 E, 1회)이
            # 있어 SessionStart 에만 기대지 않는다 — 올리는 쪽은 약한 근거로도 된다.
            self.agent = agent
        if (self.status, self.agent, self.last_event) != before:
            registry.changed(self)

    def seen(self) -> None:
        """브라우저가 봤다. done 만 idle 로 — waiting 은 쳐다본다고 안 꺼진다."""
        if self.status == "done":
            self.status = "idle"
            registry.changed(self)
        elif self.status == "unknown" and self.derived == "done":   # 제목으로 켜진 done (#38)
            self.derived = "idle"
            registry.changed(self)

    # ── 죽음 ────────────────────────────────────────
    def die(self, reason: str) -> None:
        if self.closed:
            return
        self.closed = True
        log(f"session {self.id} gone: {reason}")
        if self.flush_handle is not None:
            self.flush_handle.cancel()
            self.flush_handle = None
        if self.title_timer is not None:
            self.title_timer.cancel()
            self.title_timer = None
        if self.out_timer is not None:
            self.out_timer.cancel()
            self.out_timer = None
        self.stop_reading()
        if self.writing:                     # add_writer 를 fd 닫기 전에 뗀다 (#1)
            try:
                asyncio.get_running_loop().remove_writer(self.master)
            except Exception:
                pass
            self.writing = False
        # 셸이 죽기 직전 낸 것(예: exit 에코)을 건진다. **예산 안에서만** — 아직 SIGHUP 을 안 보냈으니
        # 자식은 여기서도 계속 쓸 수 있고, 그러면 이 루프가 언제 끝나는지는 자식의 속도에 달린다.
        # `_on_readable` 의 PUMP_BUDGET 과 같은 이유로 상한을 둔다.
        # **안 잰 것이 아니라 재서 안 나온 것이다** — macOS 에서 `yes` 를 돌리는 pane 을 DELETE 해 봤을 때
        # 예산 없는 옛 루프도 2048바이트에서 EAGAIN 으로 스스로 끝났다(2026-09-08, 1회). 리눅스는 안 쟀다.
        # 즉 이건 관측된 멈춤을 고친 것이 아니라 상한이 없던 자리에 상한을 둔 것이다.
        drained = 0
        while drained < PUMP_BUDGET:
            try:
                chunk = os.read(self.master, 65536)
            except OSError:
                break
            if not chunk:
                break
            self.pending += chunk
            drained += len(chunk)
        if self.pending:
            self._emit(bytes(self.pending))
            self.pending.clear()
        try:
            os.close(self.master)
        except OSError:
            pass
        try:
            os.kill(self.pid, signal.SIGHUP)
        except ProcessLookupError:
            pass
        asyncio.get_running_loop().call_later(KILL_GRACE_S, reaper.kill_if_alive, self)
        for a in list(self.attached):
            try:
                a.writer.write(Frame.close())
            except Exception:
                pass
            self.detach(a)
        try:
            self.settings_path.unlink()          # pane 이 죽으면 <id>.json 을 지운다
        except OSError:
            pass
        # 링버퍼(pane 당 256KB)와 큐를 여기서 비운다. 이 세션은 곧 registry 에서 빠져 다시 붙을 길이
        # 없으니 재생할 것이 없고, **데몬은 재시작하지 않으므로**(⑦=b) 참조가 언제 풀리는지에
        # 기대지 않는다 — reaper 는 SIGCHLD 로 pid 를 거둘 때까지 이 객체를 들고 있다.
        self.ring.segs.clear()
        self.ring.size = 0
        self.pending.clear()
        self.inq.clear()
        self.carry = b""
        registry.gone(self)


# ── 자식 종료 감지 ────────────────────────────────────────────────────────────────
class Reaper:
    """SIGCHLD 가 오면 아는 pid 전부에 waitpid(WNOHANG). 시그널은 합쳐질 수 있으니 하나만 보지 않는다."""

    def __init__(self):
        self.pids: dict[int, Session] = {}

    def install(self, loop) -> None:
        loop.add_signal_handler(signal.SIGCHLD, self.reap)

    def watch(self, s: Session) -> None:
        self.pids[s.pid] = s

    def reap(self) -> None:
        for pid in list(self.pids):
            try:
                got, status = os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                got, status = pid, 0
            if got == 0:
                continue
            s = self.pids.pop(pid)
            if os.WIFEXITED(status):
                how = f"exit {os.WEXITSTATUS(status)}"
            elif os.WIFSIGNALED(status):
                how = f"signal {os.WTERMSIG(status)}"
            else:
                how = f"status {status}"
            s.die(f"shell {how}")

    def kill_if_alive(self, s: Session) -> None:
        """SIGHUP 뒤 유예(KILL_GRACE_S)가 지났는데 아직 안 거둬졌으면 SIGKILL.
        **pid 가 아니라 세션 객체로 견준다** — 유예 사이에 그 pid 가 거둬지고 재사용되면
        `pid in self.pids` 는 다시 참이 되고, 그때 죽는 것은 남의(새 세션의) 셸이다."""
        if self.pids.get(s.pid) is s:
            try:
                os.kill(s.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


# ── 캔버스 (protocol.md "캔버스", ⑪ ⑫) ──────────────────────────────────────────────
class Canvas:
    """탭 하나. 세션은 반드시 캔버스 하나에 속한다.

    데몬이 갖는 것은 id·이름·순서뿐이다 — 좌표도, 미니맵도, "지금 보고 있는 탭" 도 여기 없다
    (protocol.md "없는 것"). id 는 세션 id 와 **같은 모양**이다. 캔버스 id 에는 권한이 걸려 있지
    않지만(훅 URL 도 /pty 도 안 연다) id 모양을 하나로 두려고 같게 한다. 순번은 어느 쪽에도 안 쓴다.
    """
    __slots__ = ("id", "name", "order", "seq")

    def __init__(self, cid: str, name=None, seq: int = 1):
        self.id = cid
        self.name = name
        self.order = 0      # 레지스트리가 자리에서 다시 매긴다 — 0부터 빈틈없이, 작은 것이 왼쪽
        # 몇 번째로 만들어졌나. **이름 없는 캔버스의 이름표는 이것으로 만든다** — order 로 만들면
        # 탭을 끌어 자리를 바꾸는 순간 이름표가 서로 바뀌어, 사용자에게는 캔버스 이름이 저절로
        # 바뀐 것으로 보인다(사용자 보고 2026-09-08). 자리는 움직여도 이 수는 안 움직인다.
        # 지우고 새로 만들면 번호가 건너뛴다 — 그게 맞다. 번호는 자리표이지 순번이 아니다.
        self.seq = seq

    def to_json(self) -> dict:
        return {"id": self.id, "name": self.name, "order": self.order, "seq": self.seq}


# ── 세션 레지스트리 + /events 방송 ──────────────────────────────────────────────────
class Registry:
    def __init__(self):
        self.sessions: dict[str, Session] = {}
        #: 있었던 일 (LOG_MAX 개까지, 오래된 것부터). 세션이 사라져도 남는다 — 그래서 이름을
        #: 참조가 아니라 **그때의 값으로** 박아 둔다.
        self.log: collections.deque = collections.deque(maxlen=LOG_MAX)
        #: 탭 줄에 보이는 순서 그대로. 파이썬 dict 는 삽입 순서를 지키므로 이것이 곧 order 다 —
        #: order 를 따로 정렬해 두지 않으니 "0부터 빈틈없이" 가 깨질 자리가 없다.
        self.canvases: dict[str, Canvas] = {}
        #: 만든 차례. **되쓰지 않는다** — 지운 번호를 다시 주면 이름표가 다시 겹친다.
        self.canvas_seq = 0
        self.event_clients: set = set()

    # ── 세션 ─────────────────────────────────────────────
    def list(self) -> list[Session]:
        return sorted(self.sessions.values(), key=lambda s: s.created)

    def create(self, cwd: str, canvas=None, name=None) -> Session:
        sid = secrets.token_urlsafe(16)          # 22자, 추측 불가. 순번 금지(#29)
        # 캔버스를 모르면(또는 안 줬으면) 기본 캔버스. 세션은 캔버스 없이 존재하지 않는다(⑪).
        cid = canvas if canvas in self.canvases else self.default_canvas().id
        write_pane_settings(sid)                 # 셸이 뜨기 전에 있어야 바로 친 claude 도 찾는다
        s = Session(sid, cwd, 80, 24, cid, name)
        self.sessions[sid] = s
        reaper.watch(s)
        s.start_reading()
        self.note(s, "created", "opened")
        self.changed(s)
        log(f"session {sid} created  cwd={cwd} canvas={cid} pid={s.pid}")
        return s

    # ── 캔버스 ───────────────────────────────────────────
    def canvas_list(self) -> list:
        return list(self.canvases.values())      # 삽입 순서 = order 순

    def default_canvas(self) -> Canvas:
        """order 가 가장 앞인 캔버스. **캔버스가 없는 순간은 없다** — 데몬이 뜰 때 하나 만든다."""
        if not self.canvases:                    # 방어. 정상 경로에서는 뜰 때 이미 있다
            self.canvas_changed(self.new_canvas())
        return next(iter(self.canvases.values()))

    def _renumber(self) -> None:
        for i, c in enumerate(self.canvases.values()):
            c.order = i

    def new_canvas(self, name=None) -> Canvas:
        """**끝에 붙는다** — 있던 캔버스의 order 가 안 바뀌므로 방송은 canvas 하나면 된다."""
        self.canvas_seq += 1
        c = Canvas(secrets.token_urlsafe(16), name, self.canvas_seq)
        self.canvases[c.id] = c
        self._renumber()
        return c

    def reorder_canvases(self, ids: list) -> None:
        """지금 있는 **전부**를 새 순서로. 부르는 쪽이 정확한 재배열인지 먼저 본다.
        하나씩 바꾸는 길을 안 두는 이유는 protocol.md "캔버스" 에 있다 — 중간 상태가 남으면
        두 브라우저가 서로 다른 탭 줄을 그린다."""
        self.canvases = {i: self.canvases[i] for i in ids}
        self._renumber()

    def drop_canvas(self, cid: str) -> None:
        self.canvases.pop(cid, None)
        self._renumber()                         # 남은 것의 order 를 0부터 다시 빈틈없이

    def canvas_sessions(self, cid: str) -> list:
        return [s for s in self.sessions.values() if s.canvas == cid]

    def broadcast(self, obj) -> None:
        frame = Frame.text(obj)
        for w in list(self.event_clients):
            if w.is_closing():
                self.event_clients.discard(w)
                continue
            try:
                w.write(frame)
            except Exception:
                self.event_clients.discard(w)

    #: 적을 만한 전이. 값은 브라우저가 그대로 보여 줄 말이다.
    LOG_WORTH = {"waiting": "wants you", "done": "finished"}

    def note(self, s: Session, kind: str, what: str) -> None:
        e = {"t": time.time(), "id": s.id, "kind": kind, "what": what,
             "name": s.name, "cwd": s.cwd, "canvas": s.canvas, "agent": s.agent}
        self.log.append(e)
        self.broadcast({"t": "log", "e": e})

    def changed(self, s: Session) -> None:
        """**이전 상태는 세션이 기억한다.** 부르는 쪽에서 넘기게 하면 일곱 자리 중 하나만
        빠뜨려도 그 사건이 조용히 안 적힌다 — 빠뜨릴 수 없는 자리에 둔다."""
        st = s.eff_status()
        if st != s.logged:
            if st in self.LOG_WORTH:
                self.note(s, st, self.LOG_WORTH[st])
            s.logged = st
        self.broadcast({"t": "session", "s": s.to_json()})

    def gone(self, s: Session) -> None:
        self.note(s, "gone", "closed")
        self.sessions.pop(s.id, None)
        self.broadcast({"t": "gone", "id": s.id})

    def canvas_changed(self, c: Canvas) -> None:
        """생겼거나 이름이 바뀌었다. 만들기는 끝에 붙으므로 이 한 프레임으로 족하다."""
        self.broadcast({"t": "canvas", "c": c.to_json()})

    def canvases_changed(self) -> None:
        """순서가 바뀌었다 — order 순 전체. canvas N 개로 쪼개면 받는 쪽이 중간에 어긋난 줄을 그린다."""
        self.broadcast({"t": "canvases", "cs": [c.to_json() for c in self.canvas_list()]})

    def canvas_gone(self, cid: str) -> None:
        """**두 프레임의 순서는 계약이다** — canvas_gone 을 먼저 보내야 받는 쪽이 그 id 로 들고
        있던 탭 상태를 지운 다음 새 order 를 받는다(protocol.md "/events")."""
        self.broadcast({"t": "canvas_gone", "id": cid})
        self.canvases_changed()


registry = Registry()
reaper = Reaper()


# ── ~/.palmar 준비 (protocol.md "뜨기") ─────────────────────────────────────────────
def ensure_private_dir(p: Path) -> None:
    """0700 으로 만든다. 이미 있으면 내 것이어야 하고 group/other 쓰기 비트가 없어야 한다(#29).
    심볼릭 링크는 거부한다 — 여기 shim 이 있고 PATH 맨 앞에 온다."""
    try:
        st = os.lstat(p)
    except FileNotFoundError:
        os.mkdir(p, 0o700)
        st = os.lstat(p)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise SystemExit(f"palmard: {p} 는 디렉터리여야 한다 (심볼릭 링크 불가, #29)")
    if st.st_uid != os.getuid():
        raise SystemExit(f"palmard: {p} 의 소유자가 내가 아니다 (#29)")
    if st.st_mode & 0o022:
        raise SystemExit(f"palmard: {p} 에 group/other 쓰기 비트가 있다 — chmod 700 뒤 다시 (#29)")
    if st.st_mode & 0o077:
        os.chmod(p, 0o700)        # 0755 처럼 읽기만 열린 것은 거부 대상이 아니라 조여 준다


def write_private(path: Path, data: bytes, mode: int) -> None:
    """mode 로 새로 쓴다. 임시 파일에 쓰고 rename — 실행 중인 shim 을 반쪽으로 두지 않는다."""
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
    try:
        os.fchmod(fd, mode)
        os.write(fd, data)
    finally:
        os.close(fd)
    os.replace(tmp, path)


def write_pane_settings(sid: str) -> None:
    """pane 마다 ~/.palmar/run/<id>.json (0600). 여섯 이벤트 전부 같은 http 훅 하나."""
    url = f"http://127.0.0.1:{PORT[0]}/hook/claude?pane={sid}&token={TOKEN[0]}"
    hooks = {ev: [{"hooks": [{"type": "http", "url": url}]}] for ev in HOOK_EVENTS}
    write_private(RUN_DIR / f"{sid}.json", json.dumps({"hooks": hooks}, indent=1).encode() + b"\n", 0o600)


def acquire_single_instance_lock() -> None:
    """~/.palmar/run/lock 에 배타적 flock. 못 잡으면 이미 다른 palmard 가 이 HOME 을 쓰는 것이다 —
    두 번째가 뜨면 아래에서 token 을 새로 쓰고 run/*.json 을 전부 지워 첫째의 pane 훅이 소리 없이 빠진다(#2,
    AGENTS 원칙 3 '알아채고 알려 준다' 위반). 그래서 락을 잡은 데몬만 그 일을 하고, 못 잡으면 거부한다."""
    fd = os.open(str(RUN_DIR / "lock"), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        try:
            prev = os.read(fd, 256).decode("utf-8", "replace").strip()
        except OSError:
            prev = ""
        os.close(fd)
        raise SystemExit(f"palmard: 이미 다른 palmard 가 {PALMAR_DIR} 를 쓰고 있다"
                         f"{' — ' + prev if prev else ''} (데몬은 HOME 당 하나)")
    os.ftruncate(fd, 0)
    os.write(fd, f"pid {os.getpid()} http://127.0.0.1:{PORT[0]}\n".encode())
    LOCK_FH[0] = fd     # 데몬이 사는 동안 열어 둔다 — 닫히면 락이 풀린다


def setup_palmar_dir() -> str:
    """순서대로. 하나라도 실패하면 뜨지 않는다."""
    for d in (PALMAR_DIR, BIN_DIR, RUN_DIR):
        ensure_private_dir(d)
    acquire_single_instance_lock()   # 락을 잡은 데몬만 token 을 돌리고 *.json 을 지운다 (#2)
    token = secrets.token_urlsafe(32)
    write_private(TOKEN_FILE, token.encode() + b"\n", 0o600)
    write_private(BIN_DIR / "claude", SHIM.encode(), 0o755)
    ensure_private_dir(ZDOT_DIR)
    ensure_private_dir(BASHRC.parent)
    write_private(BASHRC, BASH_RC.encode(), 0o600)
    for name, body in ((".zshenv", ZSHENV), (".zprofile", ZPROFILE),
                       (".zshrc", ZSHRC), (".zlogin", ZLOGIN)):
        write_private(ZDOT_DIR / name, body.encode(), 0o600)
    # 고아 <id>.json — 지난 데몬의 pane 은 이제 없다(⑦=b, 핸드오프 없음).
    for f in RUN_DIR.glob("*.json"):
        try:
            f.unlink()
        except OSError:
            pass
    return token


# ── 디렉터리 (protocol.md /api/dirs) ─────────────────────────────────────────────
def owned_by_me(p) -> bool:
    """resolve() 된 경로의 소유자가 지금 uid 인가. 뿌리를 가르는 술어 하나 (#31).
    stat 은 심볼릭 링크를 따라간다 — 부르는 쪽이 이미 resolve() 한 것을 넘긴다."""
    try:
        return os.stat(str(p)).st_uid == os.getuid()
    except OSError:
        return False


def roots() -> list[Path]:
    """사용자 홈 + **내가 가진** /Users/* /home/*. 요청마다 다시 본다 — scandir 둘이라 싸다.

    남의 홈은 뿌리가 아니다(#31). WSL 에서 `aa` 로 돌리는데 `/home/bb` 가 디렉터리 레일에 떠서
    거기 셸을 열 수 있었다 — 리눅스 홈은 보통 drwxr-xr-x 라 R_OK|X_OK 만으로는 다 통과한다.
    셸은 그대로 `aa` 로 도니 권한 상승은 아니지만 남의 자리고, 무엇보다 **뿌리는 cwd 검사의
    바닥이라**(`under_roots`) 넓은 만큼 `POST /api/sessions` 가 받아 준다.
    그래서 `$HOME` 은 언제나 넣고, 나머지는 **소유자가 나인 것만** 넣는다 — 홈이 여럿인 사람은
    자기 것을 다 본다. 심볼릭 링크는 먼저 푼다(전과 같다): 링크가 남의 홈을 가리키면 그 홈으로 잰다.

    macOS 에서는 이 규칙에 `/Users/Shared`(root 소유, drwxrwxrwt)가 걸려 빠진다 — 실측 확인.
    남의 것을 안 보이게 하는 값이 그것보다 크다고 보고 넣지 않았다. 되돌리려면 여기 한 줄이다."""
    out = [HOME]
    for base in ("/Users", "/home"):
        try:
            with os.scandir(base) as it:
                for e in sorted(it, key=lambda e: e.name):
                    try:
                        if not e.is_dir(follow_symlinks=True):
                            continue
                        p = Path(e.path).resolve()
                        if not owned_by_me(p):
                            continue                    # 남의 홈 — 뿌리가 아니다 (#31)
                        if not os.access(str(p), os.R_OK | os.X_OK):
                            continue
                        if p not in out:
                            out.append(p)
                    except OSError:
                        pass
        except OSError:
            continue
    return out


def under_roots(p: Path) -> bool:
    """p 는 resolve() 된 것. 뿌리 중 하나이거나 그 아래여야 한다. startswith 금지(#29)."""
    return any(p.is_relative_to(r) for r in roots())


def git_branch(d: Path):
    """<dir>/.git/HEAD 한 줄. .git 이 파일이면(worktree) gitdir: 을 따라간다. git 은 절대 안 돌린다.
    분리 HEAD 면 짧은 해시(7자) — 브랜치 이름은 아니지만 '저장소가 아니다' 로 보이면 안 되니까."""
    try:
        g = d / ".git"
        if g.is_file():
            with open(g, "r", errors="replace") as fh:
                first = fh.readline().strip()
            if not first.startswith("gitdir:"):
                return None
            gd = Path(first[len("gitdir:"):].strip())
            head = (gd if gd.is_absolute() else d / gd) / "HEAD"
        else:
            head = g / "HEAD"
        with open(head, "r", errors="replace") as fh:
            line = fh.readline().strip()
    except OSError:
        return None
    if line.startswith("ref: "):
        ref = line[5:]
        return ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
    return line[:7] or None


def has_subdir(p: str) -> bool:
    """점으로 시작하지 않는 하위 폴더가 하나라도 있는가. 첫 것에서 멈춘다."""
    try:
        with os.scandir(p) as it:
            for e in it:
                if e.name.startswith("."):
                    continue
                try:
                    if e.is_dir(follow_symlinks=True):
                        return True
                except OSError:
                    pass
    except OSError:
        pass
    return False


def dir_entry(name: str, path: str) -> dict:
    return {"name": name, "git_branch": git_branch(Path(path)), "has_children": has_subdir(path)}


def list_dirs(path: Path) -> list[dict]:
    """폴더만, 점으로 시작하는 것은 빼고, 이름순. 이 한 폴더만 읽는다 — 트리를 훑지 않는다.
    이벤트 루프 안에서 동기로 돈다(5000개 폴더에 120~250ms 블로킹, #7). run_in_executor 로 옮기지
    않는 것은 그것이 스레드를 띄우고, 스레드가 있으면 이후 pty.fork 가 교착 위험이기 때문이다
    (palmard 는 그래서 단일 스레드다 — 파일 맨 위 주석·AGENTS). 보통 폴더는 문제없고, 사람이 펼칠 때
    한 번 도는 일이라 #7 은 안 고치고 이렇게 남긴다."""
    entries = []
    with os.scandir(path) as it:
        for e in it:
            if e.name.startswith("."):
                continue
            try:
                if not e.is_dir(follow_symlinks=True):
                    continue
            except OSError:
                continue
            entries.append(dir_entry(e.name, e.path))
    entries.sort(key=lambda x: x["name"].casefold())
    return entries


#: 폴더 이름표. (만든 시각 또는 None, [경로…]) — `find_dirs` 만 쓴다.
#: **없음은 None 이지 0.0 이 아니다.** `time.monotonic()` 의 기준점은 플랫폼이 정한다 — 이 맥에서는
#: 프로세스가 뜰 때 0 에서 출발해서, 0.0 을 "만든 적 없음" 으로 쓰면 데몬이 뜬 지 60초 동안
#: `now - 0.0 > TTL` 이 거짓이라 이름표를 아예 안 만든다(실측 2026-09-08: 켜자마자 찾으면 0건).
_FIND_INDEX: list = [None, []]
_FIND_LOCK: list = [None]


async def build_find_index() -> list:
    """뿌리 아래 폴더를 한 번 훑는다. **`FIND_YIELD` 개마다 루프에 양보한다** — 안 그러면
    훑는 동안(실측 204ms) 모든 판의 바이트가 멈춘다."""
    out: list = []
    n = 0
    for root in roots():
        stack = [(str(root), 0)]
        while stack and len(out) < FIND_MAX:
            d, depth = stack.pop()
            if depth >= FIND_DEPTH:
                continue
            try:
                with os.scandir(d) as it:
                    for e in it:
                        n += 1
                        if n % FIND_YIELD == 0:
                            await asyncio.sleep(0)
                        if (e.name.startswith(".") or e.name in FIND_SKIP
                                or e.name.endswith(FIND_SKIP_SUFFIX)):
                            continue
                        try:
                            if not e.is_dir(follow_symlinks=False):
                                continue
                        except OSError:
                            continue
                        out.append(e.path)
                        if len(out) >= FIND_MAX:
                            break
                        stack.append((e.path, depth + 1))
            except OSError:
                continue
    return out


async def find_dirs(qs: str) -> list:
    """이름표에서 찾는다. **경로를 그대로 친 경우가 먼저다** — 아는 경로를 붙여넣는 것이
    가장 흔한 쓰임이고, 그건 이름표에 없어도 (깊이 밖이어도) 답할 수 있다."""
    hits: list = []
    seen = set()

    exact = resolve_under_roots(os.path.expanduser(qs)) if qs.startswith(("/", "~")) else None
    if exact is not None and exact.is_dir():
        hits.append(dir_entry(str(exact), str(exact)))
        seen.add(str(exact))

    now = time.monotonic()
    if _FIND_INDEX[0] is None or now - _FIND_INDEX[0] > FIND_TTL_S:
        if _FIND_LOCK[0] is None:            # 여럿이 동시에 물어도 한 번만 훑는다
            _FIND_LOCK[0] = asyncio.get_running_loop().create_future()
            try:
                _FIND_INDEX[1] = await build_find_index()
                _FIND_INDEX[0] = time.monotonic()
            finally:
                fut, _FIND_LOCK[0] = _FIND_LOCK[0], None
                if not fut.done():
                    fut.set_result(None)
        else:
            try:
                await asyncio.wait_for(asyncio.shield(_FIND_LOCK[0]), 10)
            except Exception:
                pass

    low = qs.lower()
    # **가까운 것부터.** 이름이 그대로 맞는 것 → 이름이 그것으로 시작 → 이름에 든 것 → 경로에 든 것.
    # 같은 등급이면 얕은 것이 먼저다 — `~/work/api` 가 `~/…/…/…/apiservice` 보다 찾던 것일 때가 많다.
    scored = []
    for pth in _FIND_INDEX[1]:
        if pth in seen:
            continue
        name = pth.rsplit("/", 1)[-1].lower()
        if name == low:
            rank = 0
        elif name.startswith(low):
            rank = 1
        elif low in name:
            rank = 2
        elif low in pth.lower():
            rank = 3
        else:
            continue
        scored.append((rank, pth.count("/"), len(pth), pth))
    scored.sort()
    for _, _, _, pth in scored[: FIND_HITS - len(hits)]:
        hits.append(dir_entry(pth, pth))
    return hits


def resolve_under_roots(raw) -> Path | None:
    """절대 경로 문자열 → resolve() → 뿌리 아래의 디렉터리. 아니면 None."""
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        return None
    p = Path(raw)
    if not p.is_absolute():
        return None
    try:
        p = p.resolve()
        if not p.is_dir():
            return None
    except OSError:
        return None
    return p if under_roots(p) else None


# ── HTTP ─────────────────────────────────────────────────────────────────────
def http(status: int, body: bytes = b"", ctype: str = "application/json; charset=utf-8",
         head_only: bool = False) -> bytes:
    # 4xx 의 몸은 {"error": "사람이 읽는 한 줄"} 이다(protocol.md HTTP 표 아래). 몸을 안 준 4xx 는
    # 여기서 이유 문구로 채운다 — 브라우저가 토스트에 그대로 쓰므로 경로도 내부 사정도 담지 않는다.
    if status >= 400 and not body:
        body = json.dumps({"error": REASONS.get(status, "Error")}).encode()
    # frame-ancestors 'none' + X-Frame-Options: DENY — 남의 페이지가 palmar UI 를 iframe 으로 감싸
    # 클릭재킹/키 입력 유도를 못 하게(#10). HEAD 응답은 헤더만, Content-Length 는 남긴다(#8).
    head = (f"HTTP/1.1 {status} {REASONS.get(status, 'Unknown')}\r\n"
            f"Content-Length: {len(body)}\r\nCache-Control: no-store\r\nConnection: close\r\n"
            "X-Frame-Options: DENY\r\nContent-Security-Policy: frame-ancestors 'none'\r\n")
    if body:
        head += f"Content-Type: {ctype}\r\n"
    return head.encode() + b"\r\n" + (b"" if head_only else body)


def http_json(status: int, obj) -> bytes:
    return http(status, json.dumps(obj).encode())


def http_error(status: int, msg: str) -> bytes:
    return http_json(status, {"error": msg})


def allowed_origin(headers: dict) -> bool:
    """Origin 이 없거나(같은 출처 fetch·curl·훅) 우리가 내준 것이어야 한다."""
    o = headers.get("origin")
    return o is None or o in (f"http://127.0.0.1:{PORT[0]}", f"http://localhost:{PORT[0]}")


def allowed_host(headers: dict) -> bool:
    """Host 도 같은 둘만. DNS 리바인딩(공격자 도메인 → 127.0.0.1)으로 index.html 의 토큰을 읽어 가는
    길을 막는다. protocol.md "인증" 절 참고(이 검사는 거기 올라가 있다)."""
    h = headers.get("host")
    return h is None or h in (f"127.0.0.1:{PORT[0]}", f"localhost:{PORT[0]}")


def serve_static(path: str, head_only: bool = False) -> bytes:
    name = unquote(path).lstrip("/") or "index.html"
    try:
        f = (WEB / name).resolve()
        ok = f.is_relative_to(WEB) and f.is_file()   # startswith 는 형제 디렉터리를 통과시킨다(#29)
    except (OSError, ValueError):
        ok = False
    if ok:
        body = f.read_bytes()
        suffix = f.suffix
    elif name == "index.html":
        body, suffix = PLACEHOLDER_INDEX, ".html"
    else:
        return http(404, head_only=head_only)
    if suffix == ".html":
        # 토큰은 이 길로만 브라우저에 간다.
        tag = f'<script>window.PALMAR_TOKEN="{TOKEN[0]}"</script>'.encode()
        body = body.replace(b"</head>", tag + b"</head>", 1) if b"</head>" in body else tag + body
    ctype = CONTENT_TYPES.get(suffix, "application/octet-stream")
    if ctype.startswith("text/") or ctype.endswith(("json", "javascript", "xml")):
        ctype += "; charset=utf-8"
    return http(200, body, ctype, head_only=head_only)


def parse_json_body(body: bytes):
    try:
        obj = json.loads(body.decode("utf-8")) if body else {}
    except (ValueError, UnicodeDecodeError):
        return None
    return obj if isinstance(obj, dict) else None


async def ws_accept(reader, writer, headers: dict) -> bool:
    key = headers.get("sec-websocket-key")
    if not key:
        writer.write(http(400))
        await writer.drain()
        return False
    accept = base64.b64encode(hashlib.sha1(key.encode() + WS_MAGIC).digest()).decode()
    writer.write(
        b"HTTP/1.1 101 Switching Protocols\r\n"
        b"Upgrade: websocket\r\nConnection: Upgrade\r\n"
        b"Sec-WebSocket-Accept: " + accept.encode() + b"\r\n\r\n"
    )
    await writer.drain()
    return True


async def ws_events(reader, writer, headers: dict) -> None:
    """제어 채널. 붙자마자 hello(전체 목록), 그 뒤 session/gone. 브라우저 → seen 만 받는다."""
    if not await ws_accept(reader, writer, headers):
        return
    registry.event_clients.add(writer)
    # 한 프레임 안에서 참조 무결 — 여기 실린 모든 session.canvas 는 같이 실린 canvases 안에 있다.
    # `v` 는 **프로토콜** 판이지 패키지 판이 아니다. 배포되기 시작하면 캐시된 새 페이지가 낡은
    # 데몬을 만난다 — 그때 서로 모르고 이상하게 구는 대신, 페이지가 대놓고 말하게 한다.
    writer.write(Frame.text({"t": "hello", "v": PROTOCOL, "daemon": __version__,
                             "canvases": [c.to_json() for c in registry.canvas_list()],
                             "sessions": [s.to_json() for s in registry.list()],
                             # **붙는 순간 함께 온다.** "없는 동안 무슨 일이 있었나" 를 알고 싶은 때가
                             # 정확히 붙는 순간이라, 한 번 더 물어보게 하지 않는다.
                             "log": list(registry.log)}))
    try:
        while True:
            msg = await read_message(reader, writer)
            if msg is None:
                break
            opcode, payload = msg
            if opcode != 0x1:
                continue
            try:
                obj = json.loads(payload)
            except ValueError:
                continue
            if isinstance(obj, dict) and obj.get("t") == "seen":
                s = registry.sessions.get(str(obj.get("id")))
                if s:
                    s.seen()
    finally:
        registry.event_clients.discard(writer)


async def ws_pty(reader, writer, headers: dict, q: dict, s: Session) -> None:
    """pane 채널. hello + 재생(또는 흔들기), 그 뒤 바이너리 = PTY 바이트. 브라우저 → 키·resize·ack."""
    if not await ws_accept(reader, writer, headers):
        return
    cols = clamp_int(qget(q, "cols"), 0, 1, MAX_COLS) or None
    rows = clamp_int(qget(q, "rows"), 0, 1, MAX_ROWS) or None
    frm = clamp_int(qget(q, "from"), 0, 0, 1 << 62)
    a = s.attach(writer, cols, rows, frm)
    try:
        while True:
            msg = await read_message(reader, writer)
            if msg is None or s.closed:
                break
            opcode, payload = msg
            if opcode == 0x2:            # 바이너리 = 키 입력. 그대로 PTY 에 쓴다
                s.send_input(payload)
            elif opcode == 0x1:          # 텍스트 = 제어
                try:
                    m = json.loads(payload)
                except ValueError:
                    continue
                if not isinstance(m, dict):
                    continue
                t = m.get("t")
                if t == "resize":         # 이것만이 행·열을 바꾼다
                    s.resize(clamp_int(m.get("cols"), s.cols, 1, MAX_COLS),
                             clamp_int(m.get("rows"), s.rows, 1, MAX_ROWS))
                elif t == "ack":
                    s.ack(a, clamp_int(m.get("n"), 0, 0, 1 << 62))
    finally:
        s.detach(a)


async def handle_request(reader, writer) -> None:
    try:                                          # 놀거나 거짓말하는 연결이 fd 를 영원히 물지 않게 (#6)
        request = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), REQUEST_TIMEOUT)
    except asyncio.TimeoutError:
        return
    lines = request.decode("latin-1").split("\r\n")
    try:
        method, target, _version = lines[0].split(" ", 2)
    except ValueError:
        writer.write(http(400))
        return
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()

    url = urlparse(target)
    q = parse_qs(url.query)
    path = unquote(url.path)

    # ── 인증 — 모든 요청에 ──
    if not (allowed_origin(headers) and allowed_host(headers)):
        writer.write(http(403))
        return
    # 바이트로 견준다 — hmac.compare_digest 는 비-ASCII str 에 TypeError 를 낸다(?token=%C3%A9) (#4)
    token_ok = hmac.compare_digest(qget(q, "token", "").encode("utf-8", "surrogatepass"), TOKEN[0].encode())

    if headers.get("upgrade", "").lower() == "websocket":
        if not token_ok:
            writer.write(http(403))
            return
        if path == "/events":
            await ws_events(reader, writer, headers)
        elif path.startswith("/pty/"):
            s = registry.sessions.get(path[len("/pty/"):])
            if s is None:
                writer.write(http(404))     # protocol.md 는 모르는 id 를 말하지 않는다 — 업그레이드 전에 404
                return
            await ws_pty(reader, writer, headers, q, s)
        else:
            writer.write(http(404))
        return

    body = b""
    if method in ("POST", "PUT", "PATCH"):
        try:
            n = int(headers.get("content-length", "0") or 0)
        except ValueError:
            writer.write(http(400))
            return
        if n > MAX_BODY:
            writer.write(http(413))
            return
        try:
            body = await asyncio.wait_for(reader.readexactly(n), REQUEST_TIMEOUT) if n > 0 else b""
        except asyncio.TimeoutError:                # Content-Length 만큼 안 보내는 연결 (#6)
            writer.write(http(408))
            return

    # ── 훅 — 항상 200 {} (훅은 0 으로 끝나야 한다). 토큰이 틀리면 상태를 바꾸지 않을 뿐이다.
    if path == "/hook/claude":
        if method != "POST":
            writer.write(http(405))
            return
        if token_ok:
            s = registry.sessions.get(qget(q, "pane", ""))
            if s is not None:
                s.on_hook("claude", parse_json_body(body))
        writer.write(http(200, b"{}"))
        return

    if path == "/api/sessions":
        if method == "GET":
            writer.write(http_json(200, [s.to_json() for s in registry.list()]))
        elif method == "POST":
            if not token_ok:
                writer.write(http(403))
                return
            obj = parse_json_body(body)
            if obj is None:
                writer.write(http_error(400, "body must be a JSON object"))
                return
            raw = obj.get("cwd")
            cwd = HOME if raw is None else resolve_under_roots(raw)   # cwd 가 없으면 홈 (스파이크 D 의 기본값)
            if cwd is None:
                writer.write(http_error(400, "cwd must be an absolute directory under a root"))
                return
            try:
                name = clean_name(obj.get("name"))
            except ValueError as e:
                writer.write(http_error(400, str(e)))
                return
            cid = obj.get("canvas")
            if cid is None:
                # 데몬에는 "지금 보고 있는 캔버스" 가 없다(브라우저 둘이 다른 탭을 볼 수 있다).
                # 안 주면 order 가 가장 앞인 캔버스 — curl 한 줄이 계속 돌게 하는 기본값이다.
                cid = registry.default_canvas().id
            elif not isinstance(cid, str) or cid not in registry.canvases:
                writer.write(http_error(400, "unknown canvas"))
                return
            s = registry.create(str(cwd), cid, name)
            writer.write(http_json(201, s.to_json()))
        else:
            writer.write(http(405))
        return

    m = re.fullmatch(r"/api/sessions/([A-Za-z0-9_\-]+)", path)
    if m:
        if method not in ("DELETE", "PATCH"):
            writer.write(http(405))
            return
        if not token_ok:                 # 이름 바꾸기·캔버스 옮기기도 예외가 아니다(protocol.md "인증")
            writer.write(http(403))
            return
        s = registry.sessions.get(m.group(1))
        if s is None:
            writer.write(http(404))
            return
        if method == "DELETE":
            s.die("deleted")
            writer.write(http(204))
            return
        # PATCH — **몸에 있는 키만** 바꾼다. 이름도 캔버스 이동도 session 프레임 하나로 나간다:
        # Session 이 둘 다 들고 있고 받는 쪽은 지금도 session 을 통째로 갈아 끼운다.
        obj = parse_json_body(body)
        if obj is None:
            writer.write(http_error(400, "body must be a JSON object"))
            return
        new_name = s.name
        if "name" in obj:
            try:
                new_name = clean_name(obj.get("name"))
            except ValueError as e:
                writer.write(http_error(400, str(e)))
                return
        new_canvas = s.canvas
        if "canvas" in obj:
            cid = obj.get("canvas")
            # 경로의 세션은 있으니 모르는 캔버스는 404 가 아니라 400 이다(protocol.md HTTP 표).
            if not isinstance(cid, str) or cid not in registry.canvases:
                writer.write(http_error(400, "unknown canvas"))
                return
            new_canvas = cid
        if (new_name, new_canvas) != (s.name, s.canvas):
            s.name, s.canvas = new_name, new_canvas
            registry.changed(s)          # 낸 쪽도 방송을 되받는다 — 브라우저는 id 로 멱등하게 반영한다
        writer.write(http_json(200, s.to_json()))
        return

    if path == "/api/canvases":
        if method == "GET":
            writer.write(http_json(200, [c.to_json() for c in registry.canvas_list()]))
        elif method == "POST":
            if not token_ok:
                writer.write(http(403))
                return
            obj = parse_json_body(body)
            if obj is None:
                writer.write(http_error(400, "body must be a JSON object"))
                return
            try:
                name = clean_name(obj.get("name"))
            except ValueError as e:
                writer.write(http_error(400, str(e)))
                return
            c = registry.new_canvas(name)
            registry.canvas_changed(c)
            writer.write(http_json(201, c.to_json()))
        else:
            writer.write(http(405))
        return

    # **/api/canvases/order 를 /api/canvases/<id> 보다 먼저 맞춘다**(protocol.md "캔버스").
    # id 는 22자라 "order" 와 부딪힐 수 없지만, 갈아 끼울 구현이 다르게 짜지 않도록 순서를 지킨다.
    if path == "/api/canvases/order":
        if method != "POST":
            writer.write(http(405))
            return
        if not token_ok:
            writer.write(http(403))
            return
        obj = parse_json_body(body)
        if obj is None:
            writer.write(http_error(400, "body must be a JSON object"))
            return
        ids = obj.get("order")
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            writer.write(http_error(400, "order must be an array of canvas ids"))
            return
        if sorted(ids) != sorted(registry.canvases):
            # 빠짐·더함·중복. 그 사이 누가 캔버스를 만들거나 지운 것이고, 그 브라우저는 이미
            # 그 이벤트를 받았으니 다시 보내면 된다.
            writer.write(http_error(409, "canvas list changed, try again"))
            return
        registry.reorder_canvases(ids)
        registry.canvases_changed()      # 한 프레임 — 순서는 집합의 성질이라 쪼개지 않는다
        writer.write(http_json(200, [c.to_json() for c in registry.canvas_list()]))
        return

    m = re.fullmatch(r"/api/canvases/([A-Za-z0-9_\-]+)", path)
    if m:
        if method not in ("PATCH", "DELETE"):
            writer.write(http(405))
            return
        if not token_ok:
            writer.write(http(403))
            return
        c = registry.canvases.get(m.group(1))
        if c is None:
            writer.write(http(404))
            return
        if method == "PATCH":
            obj = parse_json_body(body)
            if obj is None:
                writer.write(http_error(400, "body must be a JSON object"))
                return
            if "name" in obj:            # 몸에 있는 키만 바꾼다
                try:
                    name = clean_name(obj.get("name"))
                except ValueError as e:
                    writer.write(http_error(400, str(e)))
                    return
                if name != c.name:
                    c.name = name
                    registry.canvas_changed(c)
            writer.write(http_json(200, c.to_json()))
            return
        # DELETE — **빈 캔버스만, 마지막 하나는 못 지운다** (PROVISIONAL, protocol.md "캔버스").
        # 자동으로 옆 캔버스에 옮기지 않는 것은 좌표가 session id 키라 옮겨진 창이 남의 창 위에
        # 앉고 밀어내기가 돌기 때문이고, 지우면서 셸을 죽이는 길은 원칙 2 를 정면으로 어긴다.
        if registry.canvas_sessions(c.id):
            writer.write(http_error(409, "canvas still has terminals"))
            return
        if len(registry.canvases) <= 1:
            writer.write(http_error(409, "the last canvas cannot be removed"))
            return
        registry.drop_canvas(c.id)
        registry.canvas_gone(c.id)       # canvas_gone → canvases, 이 순서가 계약이다
        writer.write(http(204))
        return

    if path == "/api/dirs":
        if method == "GET":
            find = qget(q, "find", "").strip()
            if find:
                writer.write(http_json(200, {"find": find, "entries": await find_dirs(find)}))
                return
            raw = qget(q, "path")
            if not raw:
                # 뿌리 목록. path 는 null, name 은 절대 경로다 — 브라우저는 그걸 그대로 다음 path 로 쓴다.
                entries = [dir_entry(str(r), str(r)) for r in roots()]
                writer.write(http_json(200, {"path": None, "entries": entries}))
                return
            p = resolve_under_roots(raw)
            if p is None:
                writer.write(http_error(400, "path must be an absolute directory under a root"))
                return
            try:
                entries = list_dirs(p)
            except OSError as e:
                writer.write(http_error(400, f"cannot read: {e.strerror or e}"))
                return
            writer.write(http_json(200, {"path": str(p), "entries": entries}))
        elif method == "POST":
            # 만들기만 있다 — 지우기·이름 바꾸기 없음. 나머지는 옆의 터미널이 한다.
            if not token_ok:
                writer.write(http(403))
                return
            obj = parse_json_body(body)
            if obj is None:
                writer.write(http_error(400, "body must be a JSON object"))
                return
            parent = resolve_under_roots(obj.get("path"))
            name = obj.get("name")
            if parent is None:
                writer.write(http_error(400, "path must be an absolute directory under a root"))
                return
            if (not isinstance(name, str) or not name or name in (".", "..")
                    or "/" in name or "\x00" in name or len(name.encode()) > 255):
                writer.write(http_error(400, "name must be a single path component"))
                return
            target = parent / name
            try:
                os.mkdir(target)
            except FileExistsError:
                writer.write(http_error(409, "already exists"))
                return
            except OSError as e:
                writer.write(http_error(400, f"mkdir failed: {e.strerror or e}"))
                return
            writer.write(http_json(201, {"path": str(target)}))
        else:
            writer.write(http(405))
        return

    if path.startswith("/api/") or path.startswith("/hook/"):
        writer.write(http(404))
        return

    if method not in ("GET", "HEAD"):
        writer.write(http(405))
        return
    writer.write(serve_static(path, head_only=(method == "HEAD")))   # HEAD 는 몸 없이 (#8)


async def handle(reader, writer) -> None:
    try:
        await handle_request(reader, writer)
        await writer.drain()
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionResetError,
            BrokenPipeError, ValueError):
        pass
    except Exception as e:  # 한 연결의 사고가 데몬을 죽이면 안 된다 — 오래 도는 것이 전제다
        log("handler error:", repr(e))
    finally:
        try:
            writer.close()
        except Exception:
            pass


# ── 뜨기 ──────────────────────────────────────────────────────────────────────
def shutdown() -> None:
    """**우리가 연결을 닫는다. 닫히기를 기다리지 않는다.**

    파이썬 3.12.1 부터 `Server.wait_closed()` 는 열린 연결과 핸들러가 전부 끝나야 돌아온다.
    브라우저는 `/events` 를 계속 붙잡고 있으니, 그냥 기다리면 **Ctrl-C 로 안 꺼지고 탭을 닫아야만
    꺼진다.** 3.13 에서 재현했고 3.9 에서는 안 났다 — macOS 에서만 짜면 못 보는 종류다
    (사용자가 WSL 에서 먼저 봤다)."""
    writers = [a.writer for s in registry.sessions.values() for a in s.attached]
    for s in list(registry.sessions.values()):
        s.die("daemon stopping")          # pane 쪽에는 die 가 close 프레임을 이미 보낸다
    for w in list(registry.event_clients):
        try:
            w.write(Frame.close())
        except Exception:
            pass
        writers.append(w)
    registry.event_clients.clear()
    for w in writers:
        try:
            w.close()
        except Exception:
            pass


async def main(port: int) -> None:
    PORT[0] = port
    TOKEN[0] = setup_palmar_dir()
    registry.new_canvas()      # 캔버스가 없는 순간은 없다 — 이름 없는 것 하나로 뜬다 (⑪)
    loop = asyncio.get_running_loop()
    reaper.install(loop)
    try:
        # 127.0.0.1 밖으로 열지 않는다 — host 를 바꾸는 옵션을 두지 않는 것이 규칙이다(#29).
        server = await asyncio.start_server(handle, "127.0.0.1", port)
    except OSError as e:
        raise SystemExit(f"palmard: 127.0.0.1:{port} 에 묶지 못했다 — {e.strerror or e}")
    stop = loop.create_future()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: stop.done() or stop.set_result(None))
    log(f"palmard pid {os.getpid()}  shell={os.environ.get('SHELL') or '/bin/sh'}  web={WEB}"
        f"{'' if (WEB / 'index.html').is_file() else ' (index.html 없음 — 자리표를 낸다)'}")
    print(f"http://127.0.0.1:{port}", flush=True)   # 마지막 줄 — 사용자는 이것만 보고 시작한다
    await stop
    server.close()
    shutdown()
    # 닫은 것이 정리될 짧은 틈만 준다. 다 안 끝나도 나간다 — 남은 태스크는 asyncio.run 이 취소한다.
    try:
        await asyncio.wait_for(server.wait_closed(), 2.0)
    except Exception:
        pass


def doctor(port: int) -> int:
    """`palmar --doctor` — **이 코드**와 **지금 도는 데몬**과 **판마다의 상태**를 한 화면에 낸다.

    신호등이 안 켜진다는 보고를 받고 짐작으로 왕복하지 않으려고 둔다. 첫 줄에서 가리는 것이
    가장 흔한 원인이다: 받아 놓은 코드와 도는 데몬이 **다른 판**인 경우(다시 안 띄운 것).
    새 계약을 만들지 않는다 — `protocol.md` 에 이미 있는 것만 읽는다."""
    import platform, subprocess, urllib.error, urllib.request

    def out(*a):
        print(*a)

    out("palmar --doctor")
    out("─" * 64)
    out("이 코드")
    out("  버전      %s   프로토콜 %d" % (__version__, PROTOCOL))
    out("  파일      %s" % Path(__file__).resolve())
    repo = Path(__file__).resolve().parent.parent
    if (repo / ".git").exists():
        def git(*a):
            try:
                return subprocess.run(["git", "-C", str(repo)] + list(a),
                                      capture_output=True, text=True, timeout=5).stdout.strip()
            except Exception:
                return "?"
        out("  커밋      %s  (%s)" % (git("rev-parse", "--short", "HEAD"), git("log", "-1", "--format=%s")[:48]))
        out("  원격      %s" % (git("remote", "get-url", "origin") or "(없음)"))
        st = git("status", "--porcelain")
        if st:
            out("  ! 작업 트리에 안 커밋한 변경이 %d개 있다" % len(st.splitlines()))
    out("")
    out("이 기계")
    out("  python    %s  (%s)" % (platform.python_version(), sys.executable))
    out("  platform  %s" % platform.platform())
    out("  $SHELL    %s%s" % (os.environ.get("SHELL") or "(없음)",
                              "   ← 없으면 /bin/sh 로 떨어진다" if not os.environ.get("SHELL") else ""))
    out("  홈        %s" % PALMAR_DIR)
    out("")

    # **도는 데몬이 있나** — 락으로 가린다. 토큰 파일은 죽은 데몬의 것도 남으므로 근거가 못 된다.
    # 데몬은 사는 동안 run/lock 에 배타적 flock 을 쥐고, 그 안에 pid 와 진짜 주소를 적어 둔다.
    lock_path = RUN_DIR / "lock"
    running, note = None, ""
    if lock_path.exists():
        try:
            fd = os.open(str(lock_path), os.O_RDWR)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                running = False                 # 우리가 잡았다 = 아무도 안 쥐고 있다
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                running = True                  # 누가 쥐고 있다 = 데몬이 산다
                try:
                    note = os.read(fd, 256).decode("utf-8", "replace").strip()
                except OSError:
                    note = ""
            os.close(fd)
        except OSError:
            pass

    if running is False or not TOKEN_FILE.exists():
        out("도는 데몬   **없다**")
        if TOKEN_FILE.exists():
            out("  (%s 는 남아 있지만 지난 번 데몬의 것이다 — 살아 있다는 뜻이 아니다)" % TOKEN_FILE.name)
        out("")
        out("→ `python3 -m palmar` 로 띄우고 다시 이 명령을 돌려라.")
        return 1

    token = TOKEN_FILE.read_text().strip()
    if note:
        out("도는 데몬   %s" % note)
        m = re.search(r":(\d+)", note)
        if m and int(m.group(1)) != port:
            out("  ! 그 데몬은 **포트 %s** 다. 지금 물어본 것은 %d 였다." % (m.group(1), port))
            out("    → `python3 -m palmar --doctor --port %s` 로 다시." % m.group(1))
            out("")
            port = int(m.group(1))
    base = "http://127.0.0.1:%d" % port

    def get(path):
        r = urllib.request.Request(base + path + ("&" if "?" in path else "?") + "token=" + token,
                                   headers={"Origin": base})
        with urllib.request.urlopen(r, timeout=5) as f:
            return json.loads(f.read() or b"null")

    try:
        sessions = get("/api/sessions")
    except Exception as e:
        out("  ! 락은 잡혀 있는데 127.0.0.1:%d 에 못 붙었다 — %s" % (port, e))
        out("    데몬이 뜨는 중이거나, 막 죽었거나, 다른 주소에 묶였다.")
        return 1

    ver = _daemon_hello_version(port, token)
    out("")
    if ver is None:
        out("  버전      **말하지 않는다** — 프로토콜 판 이전의 낡은 데몬이다")
        out("  ! 지금 받아 둔 코드로 다시 띄워야 한다(그 데몬은 옛 코드다)")
    else:
        same = ver.get("daemon") == __version__ and ver.get("v") == PROTOCOL
        out("  버전      %s   프로토콜 %s   %s"
            % (ver.get("daemon"), ver.get("v"), "(이 코드와 같다)" if same else "**이 코드와 다르다**"))
        if not same:
            out("  ! 도는 데몬이 이 코드가 아니다 — 껐다 다시 띄워라. 고친 것이 안 들어가 있다.")
    out("  세션      %d개" % len(sessions))
    out("")
    out("판마다")
    if not sessions:
        out("  (없다 — 브라우저에서 터미널을 하나 열고 다시 돌려라)")
        return 0

    WATCH_S, STEP = 10.0, 0.5
    out("  %d초 동안 지켜본다 — 한 장만 찍으면 '지금 이 값' 은 보여도" % WATCH_S)
    out("  **움직이는지** 는 안 보인다. 그동안 판에서 에이전트에게 일을 시켜라.")
    out("")
    trail = {s["id"]: [] for s in sessions}
    end = time.monotonic() + WATCH_S
    while time.monotonic() < end:
        try:
            for s in get("/api/sessions"):
                if s["id"] in trail:
                    trail[s["id"]].append((s.get("status") or "?")[0])
        except Exception:
            break
        time.sleep(STEP)

    for s in sessions:
        seen = trail.get(s["id"]) or []
        moved = len(set(seen)) > 1
        title = s.get("title")
        out("  %s" % (s.get("name") or s.get("cwd")))
        out("      지금      status=%s" % s.get("status"))
        out("      읽는 근거  %s" % (
            "훅 — %s 가 직접 알려 준다 (가장 정확하다)" % s.get("agent") if s.get("agent")
            else ("창 제목 — 이 판은 제목을 쓴다: %r" % title if title
                  else "출력 활동 — 이 판은 창 제목을 안 쓴다")))
        out("      %s" % ("agent 칸은 훅이 채운다. 훅이 없는 에이전트면 비어 있는 것이 맞고, "
                          "상태와는 상관이 없다." if not s.get("agent") else "훅이 붙어 있다."))
        out("      %.0f초 동안  %s   %s" % (WATCH_S, " ".join(seen) or "(못 읽음)",
                                          "← 움직인다" if moved else "← **한 번도 안 바뀌었다**"))
        if not moved:
            out("        (i=idle w=working d=done  — 지켜보는 동안 그 판에서 정말 일이 돌았나?)")
        out("")
    out("무엇을 보면 되나")
    out("  · 위 줄이 **안 바뀌었다** 면: 일을 시키는 동안 쟀는지 먼저 보고, 그래도 안 바뀌면")
    out("    이 출력을 그대로 보내라. 데몬이 무엇을 보는지가 거기 다 있다.")
    out("  · '도는 데몬' 에 **이 코드와 다르다** 가 있으면 그것부터다 — 다시 띄워라.")
    return 0


def _daemon_hello_version(port: int, token: str):
    """/events 에 붙어 hello 한 장만 읽는다. 새 계약을 안 만들려고 있는 길로 묻는다."""
    import base64 as _b64, socket as _sock, struct as _st
    try:
        c = _sock.create_connection(("127.0.0.1", port), timeout=5)
        c.settimeout(5)
        key = _b64.b64encode(os.urandom(16)).decode()
        c.sendall(("GET /events?token=%s HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n"
                   "Origin: http://127.0.0.1:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                   "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n"
                   % (token, port, port, key)).encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += c.recv(4096)
        buf = buf.split(b"\r\n\r\n", 1)[1]
        def need(n):
            nonlocal buf
            while len(buf) < n:
                buf += c.recv(65536)
            o, buf = buf[:n], buf[n:]
            return o
        for _ in range(8):
            h = need(2)
            ln = h[1] & 0x7F
            if ln == 126: ln = _st.unpack("!H", need(2))[0]
            elif ln == 127: ln = _st.unpack("!Q", need(8))[0]
            pay = need(ln)
            if (h[0] & 0x0F) == 1:
                m = json.loads(pay)
                if m.get("t") == "hello":
                    c.close()
                    return m if "v" in m else None
        c.close()
    except Exception:
        pass
    return None


def cli() -> None:
    """`palmar` 명령과 `python3 -m palmar` 가 둘 다 여기로 온다."""
    ap = argparse.ArgumentParser(prog="palmar", description="palmar 데몬. 127.0.0.1 에만 묶인다.")
    ap.add_argument("--port", type=int, default=8801)
    ap.add_argument("--version", action="version", version="palmar " + __version__)
    ap.add_argument("--doctor", action="store_true",
                    help="이 코드·도는 데몬·판마다의 상태를 찍고 나간다")
    args = ap.parse_args()
    if args.doctor:
        raise SystemExit(doctor(args.port))
    asyncio.run(main(args.port))

