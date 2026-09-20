#!/usr/bin/env python3
"""palmard — the palmar daemon (#22). The contract is `docs/protocol.md`. This file implements only it.

It spawns PTYs, moves bytes, takes hooks, reads folders. Nothing more (AGENTS.md principle 1).

- Standard library only. Python 3.9 syntax only — `/usr/bin/python3` is 3.9.6, the ground for "zero install".
- Single-threaded asyncio **on POSIX** — `pty.fork` warns when threads exist and risks deadlock
  (spike D investigation), so nothing here starts a thread where fork is how a pane is made.
  Windows has no such call and no way to wait on a ConPTY either, so a pane there reads on a thread
  and hands the bytes back with `call_soon_threadsafe` (#29 step 2). `Pty.blocking` is the only
  thing that decides, and it decides once.
- The websocket is hand-rolled RFC 6455 — `Frame`/`read_frame` are spike D verbatim.
- The flow-control constants (100KB/10KB/256KB) and the coalescing time (5ms) are spike D measurements.
- Ring buffer (absolute offset + `since`) from spike G; alt-screen detection and the SIGWINCH shake from spike F.
- Canvases (⑪) and names (⑫) joined the contract on 2026-09-08. The daemon holds only id, name, order
  and a session's canvas — no minimap, no list folding, no "the tab in view" (those are the browser's).

    python3 -m palmar            # 127.0.0.1:8801. It takes --port only. There is no host option (#29).

Child exit is detected by SIGCHLD → `waitpid(WNOHANG)`. kqueue NOTE_EXIT is macOS-only, so it would need
a separate Linux fallback, and 0.5s polling eats idle (principle 6). asyncio hands the signal into the
loop over a self-pipe, so the single thread stays single. A PTY hitting EOF arrives at the same place.
"""

from __future__ import annotations

import argparse
import errno
import os
import sys

# **A platform we do not support must not be a traceback.** The check sits **above** the import
# block so a missing module cannot turn "not supported yet" into an ImportError stack — and above
# the Python-version check below, which was equally unreachable.
#
# `PALMAR_WINDOWS_ANYWAY` is **a development escape hatch, not a feature.** The port is being built
# in steps and each round has to get further than the last, so there has to be a way to say "go on
# and tell me what breaks". An environment variable rather than a flag, because nobody should find
# it by reading `--help`. **Step 4 deletes this whole block**, hatch and all (docs/windows.md).
#
# `pty` and `termios` used to be on that list too. They are gone from here as of #29 step 1: the pty
# lives behind palmar/posixpty.py (palmar/conpty.py on Windows) and this file talks to an object.
# What still pins the daemon to POSIX is the rest of the list in docs/windows.md — `flock`, the signal
# handlers, `os.kill`/`waitpid`, and `add_reader`, which has no Windows equivalent at all.
# **Windows consoles are not UTF-8 by default.** They are cp1252 or another code page, and every
# sentence palmar prints for a person is Korean — so the first refusal it tried to print died with
# UnicodeEncodeError instead (measured on a runner, 2026-09-14). Reconfiguring is one line and has
# to happen before anything can print. errors="replace" so a console that still cannot show a
# character loses the character rather than the message.
if sys.platform == "win32":                      # pragma: no cover - Windows console encoding
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

if sys.platform == "win32" and not os.environ.get("PALMAR_WINDOWS_ANYWAY"):
    raise SystemExit(
        "palmar does not run natively on Windows yet.\n"
        "  Run the daemon inside WSL and open the address it prints in your Windows browser.\n"
        "  The native port is tracked at https://github.com/maengyo/palmar/issues/29\n"
        "\n"
        "  The port is far enough along to try, if you want to help find what is missing:\n"
        "      $env:PALMAR_WINDOWS_ANYWAY=1 ; palmar\n"
        "  A daemon comes up and serves the page, terminals open, the hooks attach through a\n"
        "  PowerShell preamble, and a pane says where it is and what it is doing (2026-09-18).\n"
        "  What is not there yet: it stays attached to the terminal (no fork on Windows), and a\n"
        "  pane opened with cmd.exe gets no hooks. docs/windows.md has the list."
    )
if sys.version_info < (3, 9):
    raise SystemExit("palmar: Python 3.9 or newer is required (/usr/bin/python3 is 3.9.6)")

import asyncio
import base64
import collections
import hashlib
import hmac
import json
import math
import re
import secrets
import shutil
import signal
import socket
import stat
import struct
import subprocess
import threading
import time
from pathlib import Path
import urllib.request
from urllib.parse import parse_qs, unquote, urlparse

from . import PROTOCOL, __version__
# **One seam instead of thirty-nine.** The daemon talks to a Pty object, never to a master fd —
# palmar/posixpty.py here, palmar/conpty.py on Windows (#29 step 1, docs/windows.md). `blocking` is the
# only thing that ever has to be branched on, and step 2 is what starts branching on it.
# **The seam picks itself, once.** Everything below says `Pty()` and never asks which platform it is
# on -- that was the whole point of moving fifteen `self.master` uses behind an object (#29 step 1).
if sys.platform == "win32":                      # pragma: no cover - chosen by platform
    from .conpty import ConPty as Pty, default_shell as Pty_default_shell
else:
    from .posixpty import PosixPty as Pty, default_shell as Pty_default_shell
# `fcntl` was the last import here that simply does not exist on Windows. The seven flock calls were
# all one pattern, so they went behind a seam too (#29 step 3).
from .locking import BINARY, NOFOLLOW, release as unlock_fd, take as lock_fd

WS_MAGIC = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# ── Spike D measurements. To change one, fix protocol.md first ─────────────────────────
#: Once this many unacked bytes pile up, stop reading that pane's PTY. ttyd: 100KB x 10, VS Code: 100KB.
HIGH_WATER = 100_000
#: Read again once it comes back down to here. VS Code uses 5KB.
LOW_WATER = 10_000
#: Max bytes to swallow in one wake-up. Without it an endless spewer holds the event loop (polycanv).
PUMP_BUDGET = 256 * 1024
#: Gather PTY bytes this long before sending. macOS PTYs read 1KB at a time; without it frames flood.
COALESCE_MS = 5
#: ⑨ — the ring buffer is 256KB per pane.
RING = 256 * 1024
#: Cap on ring-buffer segments, so an app that crosses in and out of alt often cannot grow them forever.
RING_MAX_SEGS = 64
#: Spike F — (rows, cols-1) → 50ms → (rows, cols).
SHAKE_MS = 50
#: Cap on a websocket message the browser can send. The length field goes to 2^63, so it needs a cap.
MAX_WS_MESSAGE = 16 * 1024 * 1024
#: How many **not yet flushed** bytes may pile up on one connection. Past this, cut the connection.
#: `write()` returns at once and what did not go out is held by the transport, so writing on and on to a
#: peer that is not reading grows that queue without bound — and that memory is the daemon's, so
#: **other panes die with it.** The application-level ACK flow control (spike D) only listens to an honest
#: peer: one that does not read but invents ACKs walks straight through. This is the last backstop below.
WS_QUEUE_MAX = 8 * 1024 * 1024
#: Control frames (close/ping/pong) are 125 bytes or less and unsplittable per RFC 6455. Without that,
#: one 16MB ping can pull a pong of the same size out of us.
#: Cap on the HTTP request body.
MAX_BODY = 1024 * 1024
#: Cap on the pane input queue. If the PTY slave is not draining, pile up to here, then drop (logged) (#1).
INPUT_MAX = 1024 * 1024
#: Close if request line, headers and body do not arrive within this. It does not apply to websocket frames (#6).
REQUEST_TIMEOUT = 10
#: When killing a session, SIGHUP then this long, then SIGKILL — no zombie or ghost shells in a long-lived daemon.
KILL_GRACE_S = 2.0
#: How long `--stop` waits for the daemon to let go of the lock before giving up on it.
STOP_WAIT_S = 10.0

ALT_ON = b"\x1b[?1049h"
ALT_OFF = b"\x1b[?1049l"

#: Agents carry busy in the **window title** (#38, measured 2026-09-08). OSC 0/1/2 = set the title.
#: codex prefixes a braille spinner (⠋⠙⠹…), Claude Code ◐◑, and drops it once it goes idle.
#: **No character table** — the moment a new agent appears that breaks. Instead watch only for the title
#: changing over and over: a spinner changes by definition. Measured rates: codex ~12/s, Claude Code ~1/s,
#: so a 3-second window covers both. Requiring two is what keeps a one-shot title change (Claude Code
#: renames the window to the turn's words when a turn starts) from reading as "it is spinning".
OSC_TITLE = re.compile(rb"\x1b\][012];([^\x07\x1b]{0,255})(?:\x07|\x1b\\)")
#: **The two things palmar reads out of the byte stream**, in one pass so the carry cannot disagree
#: with itself. Group 2 is a window title; group 3 is a shell integration mark (OSC 133).
OSC_SEEN = re.compile(
    rb"\x1b\](?:([012]);([^\x07\x1b]{0,255})|133;([A-D])(?:;[^\x07\x1b]{0,255})?)(?:\x07|\x1b\\)")
#: A title that begins with this is palmar's preamble saying where the pane is, not an agent at work.
TITLE_CWD = "palmar:cwd:"
TITLE_WINDOW_S = 3.0
TITLE_BUSY_N = 2
#: **The count alone is not enough — how far it spreads matters too.** A shell that swaps the title on
#: every command (oh-my-zsh/p10k preexec/precmd, plain bash on WSL) changes it two or three times per
#: command, and all of it finishes **within 9ms**. Read that as a spinner and merely leaving a pane open
#: keeps it done — on screen, "it wants you" — and worse, `title_spun` latches and shuts the fallback
#: layer off for good. Measured 2026-09-09: an empty pane on such a shell was done for a full 12 seconds.
#: A spinner spins **on and on** by definition (codex ~12/s, Claude Code ~1/s — both fill the window).
#: A per-command title swap is instantaneous. This value splits the two — same idea as OUT_MIN_S below.
TITLE_MIN_S = 0.5
OSC_CARRY_MAX = 512          # cap so carry cannot grow while unterminated ESC] keeps arriving

#: Fallback for agents that never touch the title (#22). **Such agents really exist** — measured
#: (2026-09-08, `dev/probe-agent.py`): `window title (OSC 0/1/2): 0 changes`. Some set it once and never
#: again. Neither is recognised by name — whatever shows up is read by the same rules.
#: Two pieces of evidence, tied together:
#:   · `tcgetpgrp(master)` — the shell itself in the foreground means **nothing is running** (measured,
#:     and it split exactly). This keeps a bare shell out of the lights. Without it a pane holding
#:     nothing but a prompt lights up as "finished".
#:   · Output activity — something running and printing recently is working; printing then stopping wants you.
#: **The limit is written down:** an agent that thinks for a long time without printing cannot be told
#: apart from a finished one. This evidence cannot know that in principle — which is why hooks and
#: titles win wherever an agent has them.
OUT_QUIET_S = 5.0
#: Output counts as working only when it **runs on this long**. One burst and done (a shell prompt, a
#: finished `ls`) is not work. Without job control the prompt check below is useless; this takes its place.
OUT_MIN_S = 1.0

#: Folder find (`GET /api/dirs?find=`). The box above says it finds "sessions and folders", but the
#: browser could only filter **rows already unfolded** — a folder never unfolded was never caught. Keep
#: the promise. Sweep under the roots once, build labels, and search those. Measured (2026-09-08, a real home):
#:   depth 2 → 133 entries 1ms · depth 3 → 2,130 entries 41ms · **depth 4 → 4,172 entries 204ms**
#: 204ms **blocks the event loop for that long** — single-threaded, so every pane stops meanwhile. So the
#: sweep yields to the loop every `FIND_YIELD` entries. The labels it builds are reused for `FIND_TTL_S`.
#: What happened. **The lights only say "now"** — step away for 20 minutes and nothing anywhere said what
#: had finished, what had asked, or in which order. Only the last state was there.
#: **The daemon holds it**: the time the browser is closed is exactly the "while you were away", and the
#: daemon is the only thing alive then. Only what is **a shame to miss** gets written — the moment it
#: called for me, the moment it finished, things appearing and disappearing.
#: idle→working and the like are not written: frequent, no shame in missing, and they bury the rest.
LOG_MAX = 200

FIND_DEPTH = 4
FIND_YIELD = 400        # yield to the loop every this many entries swept
FIND_TTL_S = 60.0
FIND_MAX = 20000        # label cap — so memory does not grow on a very wide home
FIND_HITS = 40          # how many to return at once
#: Folder names not swept. Not places a person opens a terminal, and when present they bury the whole
#: result — measured (2026-09-08): searching "work" from a macOS home filled the screen with
#: `~/Library/…/Frameworks` (there is a work inside `Frameworks`). Tool-built trees are only wide.
FIND_SKIP = frozenset((
    "Library", "Applications", "node_modules", "__pycache__", "site-packages",
    "venv", ".venv", "dist", "build", "target", "Pods", "DerivedData",
    "vendor", "bower_components", "Caches",
))
#: Bundles. **They look like folders but they are files** — nobody opens a terminal in one, and they are
#: very wide inside (measured: `~/Pictures/Photos Library.photoslibrary` alone filled the result).
FIND_SKIP_SUFFIX = (".app", ".photoslibrary", ".framework", ".bundle", ".xcodeproj",
                    ".xcworkspace", ".lproj", ".appex", ".sparsebundle", ".fcpbundle")

#: **Output that leaves nothing on screen is not work.** Some TUIs emit cursor-management sequences
#: nonstop even while sitting still — one printed the same 32 bytes ten times a second while idle
#: (measured 2026-09-08, 147 times in 15 seconds, all of them the one
#: `ESC[?25l ESC[?7l ESC[?7h ESC[0m ESC[?12l ESC[?25h`, and 0 bytes of content once the escapes came off).
#: Count that as activity and it is **working forever**, done never arrives.
#: **This is not reading the screen** — it never looks at what was written, only whether anything was.
ESC_SEQ = re.compile(rb"\x1b(?:\[[0-9;?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[()][A-Za-z0-9]|[@-Z\\-_])")
CONTENT_FAST = 512      # bigger than this and there is content, no need to check — keeps a flood off the per-byte path
#: **And inside the alt screen, drawing the same thing again is not progress either** (user's
#: decision, 2026-09-19). An agent with no hooks and no window title — one was measured — holds its
#: approval menu open by repainting it, and by the only question the layer above can ask (did bytes
#: come out?) that is identical to working. It is the same shape as the rule above: **it never looks
#: at what was written, only at whether it is the same as last time.**
#:
#: **Only in the alt screen**, so `while true; do echo x; done` still counts as work — a shell loop
#: is not in alt and a full-screen agent is. **And only for a small chunk**: a repaint is small, a
#: build log is not, and a log is not what this is for. A redraw split differently across two reads
#: simply does not match, which leaves the light where it already was.
REPEAT_MAX = 4096

# ── Paths ─────────────────────────────────────────────────────────────────────────────
HOME = Path.home().resolve()
PALMAR_DIR = Path.home() / ".palmar"        # must be the same spelling as the shim's "$HOME/.palmar"
BIN_DIR = PALMAR_DIR / "bin"
PWSH_DIR = PALMAR_DIR / "pwsh"                 # the Windows half of the shim (#30)
PWSH_FILE = PWSH_DIR / "preamble.ps1"
ZDOT_DIR = PALMAR_DIR / "zsh"      # zsh wrapper rc
BASHRC = PALMAR_DIR / "bash" / "bashrc"   # bash wrapper rc (hooked in with --rcfile)      # rc that wraps zsh. Puts PATH back at the front after the user's rc
RUN_DIR = PALMAR_DIR / "run"
TOKEN_FILE = RUN_DIR / "token"
#: The right to be handed the page. **It is not the token.** The token is minted on every start, but this
#: one stays — so a bookmark still opens after the daemon restarts (2026-09-09, the user chose this way).
KEY_FILE = RUN_DIR / "key"
#: Where to recover the address printed at start-up once it is lost. One `cat ~/.palmar/run/url` does it.
URL_FILE = RUN_DIR / "url"
#: **A recovery file, not a config.** A daemon dies — an update, a crash, a laptop that slept — and
#: with it go every pane's shell, name and folder. The shells cannot come back (⑦=b: no handoff, and
#: that stands), but *where you were* can: the canvases, and each pane's name and last directory.
#: It lives outside `run/`, which is this daemon's live state and gets swept at start-up; this file
#: is meant to outlive a daemon, which is the whole point of it. 0600 — it holds paths.
RESTORE_FILE = PALMAR_DIR / "restore.json"
#: ③ **decided (2026-09-14): where every window sits lives here.** Position, size, z, text size and
#: group membership, one object keyed by session id, written whenever a browser saves and handed to
#: every browser in the hello frame. Until now it was the browser's `localStorage`, so two browsers on
#: one daemon each had their own board — group in Safari, switch to Chrome, and nothing was grouped
#: (user, 2026-09-14). Outside `run/` because it is meant to outlive a daemon, like restore.json.
#: 0600 for the same reason as the rest of this directory. Nothing in it is a secret; it is habit.
LAYOUT_FILE = PALMAR_DIR / "layout.json"
LAYOUT_MAX_ENTRIES = 2000     # keyed by session id, and ids are never reused — a cap, not a budget
#: **What the person turned on.** Today that is one thing: the release check, the only moment palmar
#: opens a connection that leaves the machine. **Off unless asked** (2026-09-16, user) — "no network
#: at runtime" is a promise both READMEs make, and it stays true for anybody who never turns it on.
#: The switch lives with the daemon rather than in the browser because the daemon is what does the
#: asking: a `localStorage` switch would mean Chrome knew and Edge did not, and would say nothing at
#: all while no page is open. ③ moved the board out of `localStorage` on 2026-09-14 for that reason.
#: Outside `run/`, like layout.json and restore.json, because it outlives a daemon. 0600.
SETTINGS_FILE = PALMAR_DIR / "settings.json"
LATEST_URL = "https://github.com/maengyo/palmar/releases/latest"
UPDATE_FIRST_S = 60.0                 # not at start-up — the first minute belongs to the first pane
UPDATE_EVERY_S = 24 * 60 * 60.0
SETTINGS = [{}]                       # the file, read once at start-up
UPDATE_TIMER = [None]
RESTORE_EVERY_S = 10.0
RESTORE = [None]          # what the previous daemon left, read once at start-up
#: old canvas id → the id of the canvas restored in its place. **A restored canvas is a new canvas** —
#: it gets a fresh id like any other — so a pane that remembers "I was on <old id>" points at nothing
#: and would silently land on the first canvas instead (measured: a pane on `infra` came back on
#: `canvas 1`). The map is what keeps a restored workspace shaped the way it was left.
RESTORE_CV = [{}]
RESTORE_TIMER = [None]
# web/ sits **next to** this file. The path is the same whether it runs from the repo with
# `python3 -m palmar` or installed from a wheel — if the two differed you would get bugs that only appear on one side.
WEB = (Path(__file__).resolve().parent / "web").resolve()

PORT = [8801]        # global so the Origin/Host checks and the hook URL can use it
TOKEN = [""]         # minted at start-up — secrets.token_urlsafe(32)
KEY = [""]           # the right to be handed the page. Made once and kept in run/key
LOCK_FH = [None]     # single-instance lock fd — held open while the daemon lives (closing it drops the lock) (#2)

#: Size caps from spike D.
MAX_COLS, MAX_ROWS = 500, 200

#: Cap on live panes. One pane is one shell + one pty + a 256KB ring + one settings file. Without a cap,
#: repeating `POST /api/sessions` alone exhausts fds and process slots, and then **other panes die too**
#: (WSL's common fd soft limit is 1024 — nothing like this Mac's 1048576, so that side falls over first).
#: Set far above what a person uses: at 200 the rings alone are 51MB, already past what anyone can use.
#: The point is not to block, it is to **say why and stop before the floor gives out**.
MAX_PANES = 200

#: Locale safety net for a pane. xterm.js is UTF-8 only, and if the pane's shell has no UTF-8 locale the
#: shell mangles multibyte input — measured (2026-09-08, `/bin/zsh -f -i`, clearing LANG and typing "한글"):
#:   LANG=en_US.UTF-8 → `$ 한글`  ·  no LANG / LANG=C → `$ ?\x08?<0095><009c>?<0080>`
#:
#: **Python already blocks most of this.** Started under a C/POSIX locale, CPython sets
#: `LC_CTYPE=C.UTF-8` **in the environment** per PEP 538 and the child inherits it (measured: a pane
#: launched with `env -i` was `charmap=UTF-8` too — it was that way before this code existed).
#: **One hole is left: a locale that is neither C nor UTF-8.** Coercion does not fire there
#: (measured: `LANG=ko_KR.eucKR` → the pane's `charmap: eucKR`). These few lines catch only that case.
#:
#: **What the user set is never overwritten** — if it already says UTF-8 we do not touch it, and only when
#: nothing is set do we put down one `LC_CTYPE` (language, number and date formats are theirs, not ours).
LOCALE_PREF = ("C.UTF-8", "en_US.UTF-8", "en_GB.UTF-8")
UTF8_CTYPE = [None]     # picked once at start-up — whatever actually exists on this machine


def pick_utf8_locale() -> str:
    """One UTF-8 locale that **exists** on this machine. `locale -a` is 5ms, so once at start-up is cheap (measured)."""
    have = set()
    try:
        r = subprocess.run(["locale", "-a"], capture_output=True, text=True, timeout=5)
        have = {l.strip() for l in r.stdout.splitlines() if l.strip()}
    except Exception:
        pass
    low = {h.lower(): h for h in have}
    for want in LOCALE_PREF:
        if want.lower() in low:
            return low[want.lower()]
    for h in sorted(have):                       # any UTF-8 will do
        if h.lower().endswith((".utf-8", ".utf8")):
            return h
    # When `locale` is missing or nothing could be read. macOS takes `LC_CTYPE=UTF-8` as-is.
    return "UTF-8" if sys.platform == "darwin" else "C.UTF-8"


def has_utf8(env: dict) -> bool:
    """Does this environment already say UTF-8? Priority is POSIX's own: LC_ALL > LC_CTYPE > LANG."""
    for k in ("LC_ALL", "LC_CTYPE", "LANG"):
        v = env.get(k)
        if v:
            return v.lower().endswith((".utf-8", ".utf8")) or v.upper() == "UTF-8"
    return False

#: Environment-variable prefixes not passed down to the child shell. Start the daemon inside Claude Code
#: and the child claude inherits these and comes up as a nested session (measured, AGENTS.md "검증").
#: **The prefixes cover the whole identity of the agent that launched the daemon** (widened 2026-09-08).
#: The old list ("CLAUDECODE", "CLAUDE_CODE_", "CODEX_COMPANION_") was narrower than what it claimed:
#: attaching to a real pane and running `printenv` showed five —
#: AI_AGENT, CLAUDE_EFFORT, CLAUDE_PID, CLAUDE_PLUGIN_DATA, CLAUDE_OFFICE_API_URL — passed straight through
#: (measured 2026-09-08, 55 environment variables in the pane). This is the same job STRIP_ENV_EXACT below
#: does for "the launching terminal's identity is not passed down" — none of the five held a secret
#: (a 25-char id, "xhigh", a pid, a localhost URL, a path under ~/.claude/plugins), but they are not values
#: an agent inside the pane should inherit either.
#: **Not measured:** whether those five actually change how a nested claude starts was not measured. They
#: come out by rule ("the launcher's identity is not handed down"), not by measurement.
STRIP_ENV_PREFIXES = ("CLAUDECODE", "CLAUDE_", "CODEX_", "AI_AGENT")
#: Caught by the prefixes but kept — settings a user puts in their own shell, so a pane must have them too.
#: Widening the prefixes moved the old comment's exception ("keep user-set things like CLAUDE_CONFIG_DIR") here.
KEEP_ENV_EXACT = ("CLAUDE_CONFIG_DIR",)
#: The launching terminal's identity — wrong values for a pane. Launched from Terminal.app, a pane's zsh
#: went through /etc/zshrc_Apple_Terminal, printed "Restored session:", and shared ~/.zsh_sessions history
#: with the launching terminal under the same TERM_SESSION_ID (integration measurement 2026-09-07, once).
#: tmux's TMUX is the same kind of thing, so it comes out too (not measured).
STRIP_ENV_EXACT = ("TERM_SESSION_ID", "TERM_PROGRAM_VERSION", "ITERM_SESSION_ID", "TMUX", "TMUX_PANE")

#: Hook event → status (protocol.md "상태"). None means "do not change". Never decided by reading the screen.
HOOK_STATUS = {
    "SessionStart": "idle",
    "UserPromptSubmit": "working",
    "PermissionRequest": "waiting",
    "Notification": None,
    "Stop": "done",
    "SessionEnd": "unknown",
}
HOOK_EVENTS = list(HOOK_STATUS)

# protocol.md "shim" verbatim. Rewritten every time the daemon starts (0755).
# When removing its own directory from PATH it compares as a fixed string (not a regex) and takes the
# trailing-slash spelling out with it (#12):
#   the old `grep -vx "$d"` read $d as a regex, so the `.` in `.palmar` matched any character (e.g. /Xpalmar/bin),
#   and it could not remove a trailing-slash spelling like `/.palmar/bin/`, so `command -v claude` picked itself
#   (the shim) again and exec'd forever.
# ── zsh wrapper ───────────────────────────────────────────────────────────────────────
# Putting ourselves at the front of PATH is not enough — the user's rc runs later and puts its own back first.
# Swap ZDOTDIR for ours, and have our rc put PATH back at the front **after** it has sourced the user's rc.
# User files are only read. zsh looks at ZDOTDIR's .zshenv → .zprofile → .zshrc → .zlogin.
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

#: **The separator is the platform's, and it was not.** This read `f"{BIN_DIR}:{PATH}"` with the colon
#: written in, so on Windows the first entry came out as `C:\\Users\\…\\.palmar\\bin;C:\\Windows\\system32`
#: — one path that does not exist — and `.palmar\\bin` was **never on PATH at all**. Nothing there could
#: ever be found, which is half of why the lights do not move on Windows (#30). On POSIX `os.pathsep`
#: is the same colon, so this changes nothing there.
#:
#: The fallback is only reached when the environment has no PATH, which on Windows would be a broken
#: machine rather than a bare one — but answering with `/usr/bin:/bin` there is answering in the wrong
#: language, so it says what that platform would have said.
def front_of_path(bin_dir, current: str, sep: str = os.pathsep, fallback: str = "") -> str:
    """`bin_dir` first, then whatever was already there."""
    return sep.join([str(bin_dir), current or fallback or DEFAULT_PATH[0]])


DEFAULT_PATH = [r"C:\Windows\system32;C:\Windows" if sys.platform == "win32" else "/usr/bin:/bin"]

# ── PowerShell preamble ───────────────────────────────────────────────────────────────
#: **The Windows half of the shim** (#30). POSIX puts a shell script in front of `claude` on PATH;
#: Windows cannot run that file at all — no shebang, and an extension-less name is not in PATHEXT —
#: so a `claude` opened in a palmar pane got no `--settings`, no hooks, and a light that never moved.
#:
#: **A function, not a file.** PowerShell resolves a function before anything on PATH, so this shadows
#: `claude` without a second process in the way. A `.cmd` wrapper would have been the closer analogue
#: and it brings `Terminate batch job (Y/N)?` to every Ctrl-C, which in a pane running an agent is not
#: a cost worth paying. The price of a function is that it is only in this shell: a claude started by
#: a script, from `cmd.exe`, or in a nested shell does not see it, and those still get no hooks.
#:
#: **It runs after the user's profile** — PowerShell loads profiles and *then* runs `-Command`, so the
#: whole rc-wrapping dance POSIX needs (ZDOTDIR, --rcfile) is an argument order here. User files are
#: never read or written.
#:
#: **And the prompt says where the pane is.** `cwd_of` reads the process working directory, and
#: PowerShell's `Set-Location` never touches it — it moves PowerShell's own location and nothing
#: else — so however correctly that is read it answers with the folder the shell started in. Only the
#: shell can say, so the prompt says: `palmar:cwd:<path>` in the window title, which the daemon
#: already watches and now understands (`_title_cwd`).
#:
#: **Written beside the prompt, not inside it.** The escape goes out with `[Console]::Write` before
#: the prompt string is returned, so PSReadLine never measures it. A zero-width sequence inside the
#: returned string is exactly the mistake that breaks line wrapping in zsh when `%{...%}` is
#: forgotten, and there is no reason to make it here. The user's prompt runs first, so if it sets a
#: title of its own, ours is the one that lands.
#:
#: `-CommandType Application` is what stops the function finding itself. The POSIX shim does the same
#: job by taking its own directory out of PATH as a fixed string, which went wrong once (#12: the `.`
#: in `.palmar` read as a regex). Asking for a type cannot go wrong that way.
PWSH_PREAMBLE = """# palmar 가 만든 것. 고치지 마라 — 데몬이 뜰 때마다 다시 쓴다.
# 사용자 프로필이 먼저 돌고, 그다음 이 파일이 온다.

function global:claude {
  $real = Get-Command claude -CommandType Application -ErrorAction SilentlyContinue |
          Select-Object -First 1
  if (-not $real) {
    Write-Error 'palmar: claude not found on PATH'
    return
  }
  $f = $null
  if ($env:PALMAR_PANE) {
    $f = Join-Path '{run}' ("{0}.json" -f $env:PALMAR_PANE)
  }
  if ($f -and (Test-Path -LiteralPath $f)) { & $real.Source --settings $f @args }
  else { & $real.Source @args }
}

# 이 pane 이 어느 폴더에 있는지 프롬프트가 직접 말한다. PowerShell 의 Set-Location 은
# 프로세스 작업 디렉터리를 안 바꿔서, 밖에서는 읽을 방법이 없다 (#30).
if (-not $global:PalmarUserPrompt) {
  # 감싸기 전에 감쌀 것이 있어야 한다. -Command 가 도는 시점에 prompt 가 아직 없을 수 있고,
  # 그러면 래퍼가 $null 을 부르다 매번 throw 해서 D 가 영영 안 나간다 — 초록불만 남는다.
  $p = $function:prompt
  if (-not $p) { $p = { "PS $($PWD.ProviderPath)> " } }
  $global:PalmarUserPrompt = $p
  function global:prompt {
    # 사용자 것이 먼저 — 제목을 건드린다면 우리가 뒤에 온다. 남의 프롬프트가 던져도
    # 우리 표식은 나가야 한다.
    $out = $null
    try { $out = & $global:PalmarUserPrompt } catch { $out = "PS $($PWD.ProviderPath)> " }
    try {
      $e = [char]27; $b = [char]7
      # 어디에 있는지, 그리고 "직전 명령이 끝났다 · 프롬프트다" (OSC 133).
      [Console]::Write("$e]2;palmar:cwd:$($PWD.ProviderPath)$b$e]133;D$b$e]133;A$b")
    } catch {}
    $out
  }
}

# "명령이 시작됐다" (OSC 133;C). PSReadLine 이 한 줄을 다 받아 돌려주는 바로 그 자리다 —
# 프롬프트 문자열이 아니라 그 옆이라, 줄 길이 계산에 끼어들지 않는다.
if (-not $global:PalmarReadLine -and (Get-Command PSConsoleHostReadLine -ErrorAction SilentlyContinue)) {
  $global:PalmarReadLine = $function:PSConsoleHostReadLine
  function global:PSConsoleHostReadLine {
    $line = & $global:PalmarReadLine
    try {
      $e = [char]27; $b = [char]7
      [Console]::Write("$e]133;B$b")
      if ($line -and $line.Trim()) { [Console]::Write("$e]133;C$b") }
    } catch {}
    $line
  }
}
"""


#: How the preamble is reached. **Not `-File`** — that is not interactive, and not a dot-source of the
#: path either: running a `.ps1` goes through the execution policy, which is `Restricted` by default on
#: a client and would refuse it. Text turned into a script block is not a script file, so the policy
#: does not apply, and `.` in front runs it in this scope so the function survives. `-EncodedCommand`
#: would also dodge the policy and turns the command line into a base64 blob nobody can read.
#:
#: A path may hold a single quote — a person's name with an apostrophe is enough — and in a PowerShell
#: single-quoted string that is escaped by doubling it.
def pwsh_argv(shell_argv, preamble) -> list:
    lit = str(preamble).replace("'", "''")
    return list(shell_argv) + [
        "-NoExit", "-Command",
        ". ([scriptblock]::Create((Get-Content -Raw -LiteralPath '%s')))" % lit]


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

# Placeholder so GET / still answers 200 with the token while palmar/web/index.html is not there yet.
# Someone else is writing web/ — it is not built here.
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




def clamp_int(raw, default: int, lo: int, hi: int) -> int:
    try:
        v = int(raw)
    except (TypeError, ValueError, OverflowError):   # int(float('inf')) is OverflowError (#3)
        return default
    return min(hi, max(lo, v))


#: Cap on name length (protocol.md "캔버스"). **Not a measured value, a chosen one** — the daemon runs
#: a long time (⑦=b), so it just keeps unbounded strings out; it is in one place, so it is cheap to change.
NAME_MAX = 64


def clean_name(raw):
    """Name rules (canvases and sessions alike, protocol.md "캔버스"). What comes back is what is stored.

    `null`, the empty string and a whitespace-only string all mean **no name** and return None
    (= clearing a name works with either {"name": null} or {"name": ""}).
    A string is stripped: 1-64 characters, no control characters (\x00-\x1f, \x7f). Otherwise ValueError.
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


# ── Websocket framing (spike D) ───────────────────────────────────────────────────────
class Frame:
    """RFC 6455 framing. The server does not mask; client frames get unmasked."""

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
    """One frame. (fin, opcode, payload). Spike D with only a length cap added."""
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
    if opcode & 0x8:                       # control frame
        if n > 125 or not fin:
            raise ValueError("bad control frame")
    if not masked:
        # A client **must** mask (RFC 6455 §5.1). An unmasked frame is not a browser.
        raise ValueError("unmasked client frame")
    mask = await reader.readexactly(4) if masked else b""
    payload = await reader.readexactly(n) if n else b""
    if masked and n:
        # Integer XOR — a per-byte Python loop takes 1.17s on a 16MB frame and holds the event loop (every pane) (#9).
        full = (mask * (n // 4 + 1))[:n]
        payload = (int.from_bytes(payload, "big") ^ int.from_bytes(full, "big")).to_bytes(n, "big")
    return fin, opcode, payload


def queue_full(writer) -> bool:
    """Are there too many bytes not yet out on this connection = the peer is not reading.

    `asyncio`'s `write()` never blocks — what could not be sent piles up in the transport buffer. So writing
    on and on to a peer that is not reading grows daemon memory quietly (Codex review 2026-09-09: 100 pings
    of 1KB stacked 102,800 bytes of replies with 0 drains). Not one pane's problem, the whole daemon's.
    """
    t = getattr(writer, "transport", None)
    try:
        return t is not None and t.get_write_buffer_size() > WS_QUEUE_MAX
    except Exception:
        return False


async def read_message(reader, writer) -> tuple[int, bytes] | None:
    """Gathers frames into a message. Fragments (FIN=0) are joined, and a ping gets a pong back.
    (opcode, payload). None on a close frame."""
    op = None
    buf = bytearray()
    while True:
        fin, opcode, payload = await read_frame(reader)
        if opcode == 0x8:
            return None
        if opcode == 0x9:
            if queue_full(writer):
                return None            # asking on and on while not reading — cut it
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


# ── Ring buffer (spike G's Pane.since generalized to absolute-offset segments) ──────────
class Ring:
    """Byte segments carrying absolute offsets, `cap` bytes in total.

    Spike G solved `since` assuming `produced - len(ring)` is the first byte's offset. In the product,
    bytes in an alt-screen stretch do go to the client (the offset rises) but never enter the ring
    (⑨ "the buffer does not grow inside alt"), so the ring may not be contiguous with the offset.
    Each segment therefore carries its own start offset. A shell that never uses alt has one segment.
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
        """What the ring holds from byte `offset` on. If it was pushed out, from wherever it starts (spike G)."""
        out = bytearray()
        for start, buf in self.segs:
            if start + len(buf) <= offset:
                continue
            out += buf[max(0, offset - start):]
        return bytes(out)


# ── Session = pane = one PTY ──────────────────────────────────────────────────────────
class Attach:
    """One pane channel = one browser. Each counts its own unacked bytes (flow control follows the furthest behind)."""
    __slots__ = ("writer", "unacked")

    def __init__(self, writer):
        self.writer = writer
        self.unacked = 0


class Session:
    """One PTY + a ring buffer + the browsers attached. It lives even with no browser (principle 2)."""

    def __init__(self, sid: str, cwd: str, cols: int, rows: int, canvas: str, name=None):
        self.id = sid
        self.cwd = cwd
        self.cols, self.rows = cols, rows
        self.canvas = canvas    # the canvas it sits on. **Never null** — a session is always somewhere (⑪)
        self.name = name        # the name a person gave. None means the browser builds a label from the path (⑫)
        self.status = "unknown"
        self.agent = None
        self.last_event = None
        self.alt = False
        self.created = time.time()
        self.title = ""              # the window title the agent set last (#38)
        self.fg = None               # the command in the foreground, by name — None at a prompt or when unknowable
        self.title_hits = []         # recent title-change times (monotonic). Anything outside TITLE_WINDOW_S is dropped
        self.title_timer = None
        self.osc_carry = b""         # an OSC candidate straddling a chunk boundary
        self.last_paint = None       # the last small repaint inside alt — see REPEAT_MAX
        self.cwd_told = False        # has this shell ever said where it is — then we stop reading it
        self.shell_marks = False     # has this shell ever spoken OSC 133 — then the guesses stand down
        self.cmd_start = None        # monotonic time of the last 133;C, or None between commands
        self.derived = "idle"        # status read from title and output when there are no hooks
        #: Has this pane's title **actually spun**. Not "was it ever set" — a shell setting the title on
        #: every prompt is very common (that is bash's default on WSL: `\e]0;\u@\h: \w\a`), and taking that
        #: as "this pane is read by title" **turns the fallback off entirely.** A shell title does not spin,
        #: so the lights stay idle forever (user report 2026-09-08: title=`linux user@…` but status=idle.
        #: A bare zsh on macOS does not set the title, so it never showed up there).
        self.title_spun = False
        self.logged = "unknown"      # the status last written to the activity log (registry.changed reads it)
        #: When the status now showing began (wall clock, like `created`). protocol.md sorts what is
        #: waiting by it — a pane that has waited twenty minutes and one that just asked are not the
        #: same job — so it has to leave the daemon, and for a long time it did not.
        self.status_since = time.time()
        #: When a person last typed into this pane from a browser (monotonic, 0 = never). Used only
        #: to tell an approval the person gave from one something else answered — palmar shows "this
        #: pane wants you", so when the wait ends it should not claim you answered if you did not.
        #: This is not a defence: a pane can be driven from outside palmar (another agent holds the
        #: token, or edits the shim). It only keeps palmar from **saying** you approved when it does
        #: not know that you did. See docs/decisions.md and #14.
        self.typed_at = 0.0
        #: When the current wait began (monotonic). A person typing *before* the wait started does
        #: not count as answering it, so the two timestamps are compared, not just checked.
        self.waited_at = 0.0
        #: The last wait ended without a person typing here. Set in registry.changed, cleared the
        #: moment they type or a new wait begins.
        self.answered_elsewhere = False
        self.last_out = 0.0          # when bytes last came out (monotonic)
        self.out_start = 0.0         # when the output run now in progress started
        self.out_break = False       # a person typed — the next byte opens a new run
        self.out_timer = None
        #: Has the foreground process group **ever differed** from the shell. On a shell without job control
        #: (non-interactive `/bin/sh`) the child sits in the shell's own group, so `tcgetpgrp` points at the
        #: shell forever — read that as "nothing is running" and **the pane is idle even while the agent
        #: prints the whole time** (measured 2026-09-08: SHELL=/bin/sh, for a full 14 seconds).
        #: So this signal is trusted **only after it has been seen to differ at least once**.
        self.fg_varied = False

        self.ring = Ring(RING)
        self.produced = 0            # total bytes sent to clients so far (absolute offset)
        self.carry = b""             # an ESC[?1049 prefix candidate straddling a chunk boundary — decided on the next flush
        self.pending = bytearray()
        self.flush_handle = None
        self.reading = False
        self.inq = bytearray()       # input not yet written to the PTY. When the slave queue fills, add_writer writes the rest (#1)
        self.writing = False
        self.closed = False
        self.attached: list[Attach] = []
        self.settings_path = RUN_DIR / f"{sid}.json"

        #: Only used where the pty blocks — Windows. `_can_read` is back-pressure expressed as a
        #: thread that parks: clearing it stops the reads, setting it lets them go on, which is
        #: exactly what add_reader/remove_reader do on the other side.
        self._thread = None
        self._loop = None
        self._can_read = threading.Event()

        self.pty = Pty()
        self._spawn(rows, cols)
        self.pid = self.pty.pid

    def _spawn(self, rows: int, cols: int) -> None:
        # The daemon hard-codes the shell. There is no way for a client to pick the command
        # (#29, principle 4) — **and which shell is the platform's answer, not this file's.** There is
        # no $SHELL on Windows at all, so asking for it there got as far as CreateProcessW and then
        # failed with "file not found" (measured on a runner, 2026-09-14).
        argv0 = Pty_default_shell()
        shell = argv0[0]
        env = {k: v for k, v in os.environ.items()
               if k in KEEP_ENV_EXACT
               or (not k.startswith(STRIP_ENV_PREFIXES) and k not in STRIP_ENV_EXACT)}
        env["PATH"] = front_of_path(BIN_DIR, env.get("PATH"))          # shim at the front
        # Putting ourselves at the front of PATH is not enough — the user's rc runs later and puts its own back first
        # (measured: one `export PATH="$HOME/.local/bin:$PATH"` line in ~/.zshrc pushed the shim out).
        # For zsh, wrap it with ZDOTDIR so our rc runs **last**. User files are never touched.
        base = os.path.basename(shell)
        argv = list(argv0)
        if base == "zsh" and (ZDOT_DIR / ".zshrc").exists():
            env["PALMAR_USER_ZDOTDIR"] = env.get("ZDOTDIR") or str(HOME)
            env["ZDOTDIR"] = str(ZDOT_DIR)
        elif base.lower() in ("pwsh.exe", "powershell.exe") and PWSH_FILE.exists():
            argv = pwsh_argv(argv0, PWSH_FILE)
        elif base == "bash" and BASHRC.exists():
            # bash has no ZDOTDIR. --rcfile replaces an interactive shell's rc —
            # ours sources the user's first and then puts PATH back (measured 2026-09-08).
            # A login shell (-l) reads .bash_profile, so this trick misses there — the wrapper below sources that too.
            env["PALMAR_USER_BASHRC"] = str(HOME / ".bashrc")
            env["PALMAR_USER_BASH_PROFILE"] = str(HOME / ".bash_profile")
            argv = [shell, "--rcfile", str(BASHRC)]
        env["PALMAR_PANE"] = self.id
        env["TERM"] = "xterm-256color"
        # A pane must be UTF-8 (xterm.js is UTF-8 only). Set **only when nothing is set** — if it is, it is theirs.
        if not has_utf8(env):
            env["LC_CTYPE"] = UTF8_CTYPE[0] or "UTF-8"
        env["TERM_PROGRAM"] = "palmar"     # same slot tmux puts TERM_PROGRAM=tmux in. Overwrites the launching terminal name
        # Everything above is palmar's policy — which shell, which rc, which environment. The fork
        # itself, the cwd fallback, the exec and the inheritable flag are the pty's, and both sides of
        # the seam do them the same way.
        self.pty.spawn(argv, env, self.cwd, rows, cols)

    def to_json(self) -> dict:
        return {
            "id": self.id, "cwd": self.cwd, "cols": self.cols, "rows": self.rows,
            "status": self.eff_status(), "agent": self.agent, "alt": self.alt,
            "title": self.title or None,
            "fg": self.fg,               # what is running, by name (comm_of); the page labels an unnamed pane with it
            # **Which layer is speaking for this pane.** `--doctor` says how each light was worked
            # out, and with a new layer under it that line would otherwise say something untrue.
            # "shell" is on once this pane has emitted an OSC 133 mark; "running" is on between C
            # and D, which is when the layers that watch the agent are the ones still talking.
            "shell": self.shell_marks or None,
            "running": (self.cmd_start is not None) or None,
            "created": self.created, "last_event": self.last_event,
            "canvas": self.canvas, "name": self.name,
            # True when this pane's last wait ended with nobody typing here (#14). The browser reads
            # it so the live row says "answered — not here" instead of claiming you finished it.
            "answered_elsewhere": self.answered_elsewhere,
            # **Both of these are in protocol.md and neither was ever sent.** The browser reads
            # `since` to put the longest wait on top, and `quiet` to say a pane claims to be working
            # while printing nothing — and with the field absent that note could never once appear,
            # however long a pane sat silent (found auditing the roadmap, 2026-09-11).
            "since": self.status_since,
            # Seconds since bytes last came out. null while a pane has printed nothing at all: that
            # is a pane that just started, not one that has gone quiet.
            "quiet": (round(max(0.0, time.monotonic() - self.last_out), 1)
                      if self.last_out else None),
        }

    # ── PTY → browser (spike D) ──────────────────────────────────
    def start_reading(self) -> None:
        if self.reading or self.closed:
            return
        if self.pty.blocking:
            # One thread per pane, started the first time and parked afterwards rather than churned:
            # a pane can cross the water marks often, and a thread per crossing is not a flow control.
            self._loop = asyncio.get_running_loop()
            self._can_read.set()
            if self._thread is None:
                self._thread = threading.Thread(target=self._read_loop, name="pty-" + self.id,
                                                daemon=True)
                self._thread.start()
        else:
            asyncio.get_running_loop().add_reader(self.pty.fileno(), self._on_readable)
        self.reading = True

    def stop_reading(self) -> None:
        if not self.reading:
            return
        if self.pty.blocking:
            # **It parks after the read it is already in.** So the cap can be overshot by one read —
            # 64KB — which is the same shape of slack the POSIX side documents for PUMP_BUDGET.
            self._can_read.clear()
        else:
            asyncio.get_running_loop().remove_reader(self.pty.fileno())
        self.reading = False

    # ── the blocking half (#29 step 2) ───────────────────────────
    def _read_loop(self) -> None:
        """**Runs on its own thread and touches nothing the loop owns.** Every byte crosses back with
        `call_soon_threadsafe`; `self.closed` is only read, and the Event is the one thing both
        sides write. Getting that wrong is a data race in the pane buffers, so it is kept this narrow."""
        loop = self._loop
        why = "closed"
        try:
            while not self.closed:
                self._can_read.wait()
                if self.closed:
                    return
                try:
                    chunk = self.pty.read(65536)
                except OSError:
                    chunk = b""         # the console is gone — same as EOF, and die() says which
                if not chunk:
                    why = "eof"
                    loop.call_soon_threadsafe(self._read_eof)
                    return
                loop.call_soon_threadsafe(self._read_gave, chunk)
        except BaseException as e:
            # **A thread that dies quietly takes the pane's output with it and says nothing** — the
            # window just stops filling, which is indistinguishable from a shell that went quiet.
            # Anything that is not an OSError from read() lands here, and the log is where a person
            # can find it (2026-09-14).
            why = "%s: %s" % (type(e).__name__, e)
            loop.call_soon_threadsafe(log, "session %s: reader thread stopped — %s" % (self.id, why))
        finally:
            if why not in ("closed", "eof"):
                self.reading = False

    def _read_gave(self, chunk: bytes) -> None:
        """On the loop again. The tail of `_on_readable`, minus the draining — the thread did that."""
        if self.closed:
            return
        self.pending += chunk
        if self.flush_handle is None:
            self.flush_handle = asyncio.get_running_loop().call_later(COALESCE_MS / 1000, self._flush)
        self._flow()

    def _read_eof(self) -> None:
        if not self.closed:
            self.die("pty eof")

    def _backpressure(self) -> int:
        """The furthest-behind browser's unacked bytes + pending not yet sent. 0 when nobody is attached —
        the ring buffer catches it, so reading goes on (protocol.md 'when nobody is there')."""
        if not self.attached:
            return 0
        return max(a.unacked for a in self.attached) + len(self.pending)

    def _on_readable(self) -> None:
        got = 0
        while got < PUMP_BUDGET:
            if self._backpressure() >= HIGH_WATER:   # counting unsent pending too, so the cap really holds (#5)
                break
            try:
                chunk = self.pty.read(65536)
            except BlockingIOError:
                break
            except OSError:
                # Linux gives EIO when the child dies, macOS gives empty bytes (investigation).
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
        """Puts it in the ring (skipping alt stretches) and sends it to everyone attached in one frame."""
        # **Reading status must never cost a byte.** Both of these only *watch* the stream, and a
        # fault in either used to travel up through `_flush` — whose caller is a call_later, so it is
        # logged and forgotten — with `pending` already cleared. The chunk was simply gone. That is a
        # status bug presenting as an empty terminal, and it took a day to find as one (2026-09-14).
        # The lights can be wrong for a moment; the terminal cannot lose output.
        for watch in (self._scan_title, self._out_scan):
            try:
                watch(data)
            except Exception as e:
                log(f"session {self.id}: status scan failed, output unaffected — {type(e).__name__}: {e}")
        alt_changed = self._absorb(data)
        frame = Frame.build(data)
        for a in list(self.attached):
            # The ACK flow control (`_flow` above) only listens to an honest peer — one that does not read
            # but invents ACKs leaves `unacked` at 0 while the transport queue swells. Last backstop below it.
            if queue_full(a.writer):
                log(f"session {self.id}: client not reading, detaching")
                self.detach(a)
                try:
                    a.writer.close()
                except Exception:
                    pass
                continue
            try:
                a.writer.write(frame)
                a.unacked += len(data)
            except Exception:
                self.detach(a)
        self._flow()
        if alt_changed:
            registry.changed(self)   # when alt flips, send session over /events

    # ── Reading status out of the bytes (#38) ────────────
    def _scan_title(self, data: bytes) -> None:
        """Only counts. It removes nothing from the ring or from the frame going to the browser —
        xterm must receive these as-is and do its job (unlike the alt markers)."""
        buf = self.osc_carry + data
        last = 0
        hit = False
        for m in OSC_SEEN.finditer(buf):
            last = m.end()
            if m.group(3):             # OSC 133 — the shell saying it plainly
                self._shell_mark(m.group(3).decode())
                continue
            t = m.group(2).decode("utf-8", "replace")
            if self._title_cwd(t):     # palmar's own marker — a place, not a heartbeat
                continue
            if t != self.title:        # setting the same title again is not a change
                self.title = t
                self.title_hits.append(time.monotonic())
                hit = True
        rest = buf[last:]
        i = rest.rfind(b"\x1b]")
        # OSC that this rule does not catch — a hyperlink (OSC 8), say — lands here too; the cap absorbs it.
        self.osc_carry = rest[i:][-OSC_CARRY_MAX:] if i >= 0 else b""
        if hit:
            self._title_tick()
            self._arm_settle()

    def _arm_settle(self) -> None:
        """No bytes arrive at the moment the title stops — only a clock can tell that it stopped.
        Wake up **relative to the last change.** Waking on a fixed interval leaves a gap of up to one
        period between the moment it stopped and the moment we notice (measured: codex went idle at
        3.4s and it was noticed at 9.4s)."""
        if self.title_timer is not None:
            self.title_timer.cancel()
        last = self.title_hits[-1] if self.title_hits else time.monotonic()
        delay = max(0.05, TITLE_WINDOW_S - (time.monotonic() - last) + 0.05)
        self.title_timer = asyncio.get_running_loop().call_later(delay, self._title_settle)

    def _title_busy(self) -> bool:
        now = time.monotonic()
        self.title_hits = [t for t in self.title_hits if now - t <= TITLE_WINDOW_S]
        if len(self.title_hits) < TITLE_BUSY_N:
            return False
        return self.title_hits[-1] - self.title_hits[0] >= TITLE_MIN_S

    #: **OSC 133 — the shell saying it, instead of us guessing.** `A` a prompt is being drawn, `B` it
    #: is waiting for typing, `C` a command has started, `D` it finished. Every layer above this one
    #: is inference: the title spinning is a heartbeat we hope means work, and the output rule is
    #: "printing on and on = working, printing then stopping = done", which calls a long quiet think
    #: finished and a chatty log busy. A shell that emits these is telling us, and once one does the
    #: guessing stops for that pane — the same rule as hooks over the title (`eff_status`).
    #:
    #: **Done means something ran and said something.** `C` then `D` with nothing printed between is
    #: an empty line and an Enter, and lighting "finished" for that would be a light nobody asked
    #: for — the same discipline `_out_tick` and `_title_tick` already keep.
    #:
    #: **And it does not silence the layers above it — it bounds them.** The first version stood the
    #: title and output layers down for good the moment a shell spoke, which was wrong in the case
    #: that matters most: the shell knows *a command is running*, and an agent is one long command.
    #: With one such agent the light went green and stayed green for a whole session, because the
    #: layer that watches **the agent** rather than the shell had been switched off (user,
    #: 2026-09-18). So: **at a prompt the shell is the last word** — nothing may paint green over a
    #: `D`. **Between `C` and `D` the agent-watching layers speak**, because the shell has nothing
    #: more to say until the command ends and they are all there is.
    #:
    #: Hooks still win. This only ever writes `derived`, which `eff_status` reads when the hooks are
    #: silent.
    def _shell_mark(self, mark: str) -> None:
        self.shell_marks = True
        if mark == "C":
            self.cmd_start = time.monotonic()
            want = "working"
        elif mark == "D":
            started, self.cmd_start = self.cmd_start, None
            said = started is not None and self.last_out >= started
            want = "done" if said else "idle"
        else:
            # **A and B say nothing the D before them did not.** They used to set idle, and the
            # preamble writes `D` and `A` in one go — so "finished" was erased in the same breath it
            # was set and the done light could never be seen. A prompt appearing is not a state; the
            # command ending is, and `D` is that.
            return
        if want != self.derived:
            self.derived = want
            if self.status == "unknown":
                registry.changed(self)

    def _title_tick(self) -> None:
        """Spinning means working; **spinning and then** stopping means done. Nothing becomes done without
        having spun — turning a TUI sitting still (vim, say) into 'finished' leaves the lights always on."""
        if self.shell_marks and self.cmd_start is None:
            return                         # at a prompt the shell is the last word — see _shell_mark
        busy = self._title_busy()
        if busy:
            self.title_spun = True         # **only here** does the title layer take the lead
        elif not self.title_spun:
            return                         # it has not spun yet — leave the call to the fallback
        want = "working" if busy else ("done" if self.derived == "working" else self.derived)
        if want != self.derived:
            self.derived = want
            if self.status == "unknown":      # a session the hooks speak for is not shaken on screen
                registry.changed(self)

    def _title_settle(self) -> None:
        self.title_timer = None
        if self.closed:
            return
        if self._title_busy():
            self._arm_settle()
        else:
            self._title_tick()

    # ── Status from output — fallback for agents that skip the title (#22) ─────
    @staticmethod
    def _has_content(data: bytes) -> bool:
        """Does this chunk leave anything on screen? Moving the cursor alone does not."""
        if len(data) > CONTENT_FAST:
            return True
        rest = ESC_SEQ.sub(b"", data)
        return any(b >= 0x20 or b in (0x07, 0x09, 0x0a, 0x0d) for b in rest)

    def _out_scan(self, data: bytes) -> None:
        """Called whenever bytes come out. Does nothing on a session that uses the title —
        the title is more accurate, and two layers fighting over one value makes the lights flicker."""
        if not self._has_content(data):
            return                       # a cursor-management tick — counted as if it never came
        if self.alt and len(data) <= REPEAT_MAX:
            paint = ESC_SEQ.sub(b"", data)
            if paint == self.last_paint:
                return                   # the same picture again — see REPEAT_MAX
            self.last_paint = paint
        else:
            self.last_paint = None       # out of alt, or too big to be a repaint
        now = time.monotonic()
        if self.out_break or now - self.last_out > OUT_QUIET_S:
            self.out_start = now         # a person did something, or it prints again after quiet — a new run
            self.out_break = False
        self.last_out = now
        if self.title_spun:
            return
        self._out_tick()
        self._arm_out()

    def _arm_out(self) -> None:
        """No bytes arrive at the moment it stops — a clock is needed, same reason as on the title side."""
        if self.out_timer is not None:
            self.out_timer.cancel()
        delay = max(0.05, OUT_QUIET_S - (time.monotonic() - self.last_out) + 0.05)
        self.out_timer = asyncio.get_running_loop().call_later(delay, self._out_settle)

    def _out_settle(self) -> None:
        self.out_timer = None
        if self.closed or self.title_spun:
            return
        if time.monotonic() - self.last_out < OUT_QUIET_S:
            self._arm_out()                 # it printed again meanwhile
        else:
            self._out_tick()

    def _at_prompt(self) -> bool:
        """Is the foreground process group the shell itself = nothing is running.
        `pty.fork` makes the child a session leader, so the shell's pgid is its own pid.

        **Trusted only after it has been seen to differ at least once.** On a shell without job control
        this value points at the shell forever, and that means **unknowable**, not "nothing is running"."""
        # The pty answers this, because Windows has no foreground process group at all and has to be
        # able to say so. It returns None for "unknowable"; here that is False — **not knowing is not
        # being at a prompt**, which is the reading this call has always had.
        answer = self.pty.foreground_is_shell()
        self.fg_varied = self.pty.fg_varied
        return bool(answer)

    #: **The folder a pane is in, not the one it was opened in.** `to_json` handed out the cwd the
    #: pane was *created* with and nothing ever changed it, so the left rail named the opening folder
    #: for the life of the session — on every platform, not only Windows (measured 2026-09-17: a `cd`
    #: into a subfolder, and eleven seconds later the rail still said the folder it started in). The
    #: restore snapshot had been reading the live one all along with this very call, so the number was
    #: being fetched every ten seconds and thrown away.
    #:
    #: **None is not an answer, it is the absence of one.** `cwd_of` cannot see a PowerShell
    #: `Set-Location` and returns None on a platform it cannot ask, and the last place we knew about
    #: is a better answer than no place at all. Windows fills this in through the title instead.
    def sample_cwd(self) -> None:
        # **A pane that has said where it is is not corrected from outside.** These two were fighting
        # on Windows: the prompt writes the right folder into the title, and ten seconds later this
        # read the PEB — which `Set-Location` never updates — and put the folder the pane *opened* in
        # back. From the outside it looked like running an agent sent the rail home and kept it there
        # (user, 2026-09-18: "에이전트 실행하면 ~ 로 바뀌고 … 돌아와도 ~ 로 유지되네"). It was the tick,
        # not the agent. Same rule as hooks over the title, and OSC 133 over the guesses: the one who
        # knows wins, and the one who infers stops.
        if self.cwd_told:
            return
        live = cwd_of(self.pid)
        if live and live != self.cwd:
            self.cwd = live
            registry.changed(self)

    #: The pane telling us where it is, because we cannot see it: `palmar:cwd:<path>` in the window
    #: title. PowerShell's `Set-Location` moves PowerShell's own location and never calls
    #: SetCurrentDirectory, so the process working directory — which is all `cwd_of` can read — stays
    #: where the shell started (#30). Only a shell that says so can be followed, and palmar's preamble
    #: makes the prompt say so. **It is not a title spin**: it changes when the folder does, once,
    #: which is nothing like an agent working, and counting it as one would be a lie in the lights.
    def _title_cwd(self, t: str) -> bool:
        if not t.startswith(TITLE_CWD):
            return False
        path = t[len(TITLE_CWD):].strip()
        self.cwd_told = True
        if path and path != self.cwd and os.path.isdir(path):
            self.cwd = path
            registry.changed(self)
        return True

    def sample_fg(self) -> None:
        """Read who is in front and, when that changed, tell every browser. Called on every output tick
        and every ten seconds from the restore timer, so a command that prints nothing is still seen."""
        pid = self.pty.foreground_pid()
        name = None
        if pid and pid != self.pid:
            name = comm_of(pid)
            # A program installed under its own version number is named after it — see VERSIONISH.
            if name and VERSIONISH.match(name):
                name = name_from_path(exe_of(pid)) or name
        if name != self.fg:
            self.fg = name
            registry.changed(self)

    def _out_tick(self) -> None:
        """At a prompt: idle. Something running and printing **on and on**: working. Printing then stopping:
        done — nothing becomes done without ever having printed (same discipline as the title side)."""
        self.sample_fg()
        if self.shell_marks and self.cmd_start is None:
            return                          # at a prompt the shell is the last word — see _shell_mark
        now = time.monotonic()
        if self._at_prompt():
            want = "idle"
        elif now - self.last_out < OUT_QUIET_S and self.last_out - self.out_start >= OUT_MIN_S:
            want = "working"
        elif now - self.last_out < OUT_QUIET_S:
            want = self.derived             # one burst and done — not called work yet
        else:
            want = "done" if self.derived == "working" else self.derived
        if want != self.derived:
            self.derived = want
            if self.status == "unknown":
                registry.changed(self)

    def eff_status(self) -> str:
        """If the hooks speak, use what they say — hooks are exact, the title is a guess. Only agents
        without hooks are read by title (codex at work may have no hooks open — #15)."""
        return self.status if self.status != "unknown" else self.derived

    def _absorb(self, data: bytes) -> bool:
        """Spike F alt detection: ESC[?1049h → alt, ESC[?1049l → off. The markers themselves and the bytes
        inside alt do not go into the ring — the bytes from before entering stay. A prefix straddling a
        chunk boundary is carried over. True if the alt state changed."""
        changed = False
        buf = self.carry + data
        base = self.produced - len(self.carry)     # absolute offset of buf[0]
        pos = 0
        # **Only re-find the marker we passed.** Finding both every time means that when one is absent from
        # the rest of the buffer, its find scans to the end of the buffer once per marker — O(markers × size).
        # One 256KB chunk filled with unpaired `ESC[?1049h` ate **5.8 seconds** of the event loop (measured
        # 2026-09-09). While one pane prints that, other panes' bytes, the `/events` broadcast and every
        # request stand still with it. `pos` only grows, so -1 stays -1 — a miss is never searched twice.
        i_on = buf.find(ALT_ON, pos)
        i_off = buf.find(ALT_OFF, pos)
        while True:
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
            pos = i + len(ALT_ON)                  # ALT_OFF is the same 8 bytes
            if 0 <= i_on < pos:
                i_on = buf.find(ALT_ON, pos)
            if 0 <= i_off < pos:
                i_off = buf.find(ALT_OFF, pos)
        tail = buf[pos:]
        hold = 0
        for n in range(min(len(ALT_ON) - 1, len(tail)), 0, -1):
            if ALT_ON.startswith(tail[-n:]):       # ALT_OFF shares the first 7 bytes
                hold = n
                break
        body = tail[:len(tail) - hold] if hold else tail
        if not self.alt:
            self.ring.append(base + pos, body)
        self.carry = tail[len(tail) - hold:] if hold else b""
        self.produced += len(data)
        return changed

    def _flow(self) -> None:
        """Stop reading when unacked (+ unsent pending) bytes reach HIGH, read again once they fall to LOW.
        With several browsers the furthest behind decides. With nobody there, keep reading — the ring catches it."""
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

    # ── Attach / detach (protocol.md "pane 채널") ───────────────
    def attach(self, writer, cols: int | None, rows: int | None, frm: int) -> Attach:
        a = Attach(writer)
        frm = min(max(0, frm), self.produced)
        replay = b""
        if not self.alt:
            replay = self.ring.since(frm)
            if self.carry and frm < self.produced:
                # carry is bytes already sent but not yet in the ring — append it to the replay.
                carry_start = self.produced - len(self.carry)
                replay += self.carry[max(0, frm - carry_start):]
        hello = {"t": "hello", "offset": self.produced, "alt": self.alt, "replayed": len(replay)}
        writer.write(Frame.text(hello))
        if replay:
            writer.write(Frame.build(replay))
            a.unacked += len(replay)          # the browser acks this too
        self.attached.append(a)
        if self.alt:
            # Shake instead of replaying (spike F). A different size given at attach time is a real resize.
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

    # ── Browser → PTY ─────────────────────────────────
    def send_input(self, data: bytes) -> None:
        """Treats the PTY master as a stream with backpressure. os.write may write short — the master is
        non-blocking and macOS returns a partial count when the slave input queue (TTYHOG≈1024) fills
        (#1 measured: 1024/8000/70000 bytes all cut to 1022). Throw the return value away and a paste over
        1KB is silently truncated. The rest sits in inq, written once the slave drains and the fd is writable."""
        if self.closed or not data:
            return
        space = INPUT_MAX - len(self.inq)
        if space <= 0:
            log(f"session {self.id}: input queue full, dropping {len(data)} bytes")
            return
        if len(data) > space:
            log(f"session {self.id}: input queue near full, dropping {len(data) - space} bytes")
            data = data[:space]
        # **A person did something = the next bytes out begin a new output run.** `out_start` is "when the
        # run now in progress began", and breaking it on OUT_QUIET_S (5s) alone **glues two unrelated
        # outputs into one**: the prompt printed when the pane opened and the echo of a character typed 3
        # seconds later stuck together into "it printed on and on for over a second", and a pane with
        # nothing running lit up working → done 5s later (measured 2026-09-09: one `echo hi` reproduced it).
        # **Do not write `out_start = now` here** — that puts the **empty time between** the input and the
        # next output into "time spent printing on and on" (fixed that way once and a freshly opened pane
        # went working). Only raise the flag; the next byte decides the real start.
        self.out_break = True
        self.inq += data
        self._pump_input()

    def _pump_input(self) -> None:
        """Writes as much of inq as it can and waits with add_writer for the next chance if any is left. The add_writer callback calls this too."""
        if self.closed:
            return
        if self.inq:
            try:
                n = self.pty.write(self.inq)
            except BlockingIOError:
                n = 0
            except OSError:
                self.die("pty write failed")
                return
            if n:
                del self.inq[:n]
        if self.pty.blocking:
            # **Written straight through, with no add_writer.** WriteFile waits when the console's
            # input pipe is full instead of returning short, so there is nothing to wait *for* — the
            # write above already took all of it. The cost is that a paste larger than the pipe's
            # buffer stalls the loop for as long as the console takes to drain it. Input is typed or
            # pasted, not streamed, so that is bounded; if it ever shows up, it is a second thread
            # and not a redesign (#29 step 2).
            self.inq.clear()
            return
        loop = asyncio.get_running_loop()
        if self.inq and not self.writing:
            loop.add_writer(self.pty.fileno(), self._pump_input)
            self.writing = True
        elif not self.inq and self.writing:
            loop.remove_writer(self.pty.fileno())
            self.writing = False

    def resize(self, cols: int, rows: int, force: bool = False) -> None:
        if self.closed:
            return
        changed = (cols, rows) != (self.cols, self.rows)
        if not (changed or force):
            return
        try:
            self.pty.resize(rows, cols)
        except OSError:
            return
        self.cols, self.rows = cols, rows
        if changed:
            registry.changed(self)

    def shake(self, cols: int, rows: int) -> None:
        """SIGWINCH shake: (rows, cols-1) → 50ms → (rows, cols). An alt-screen app redraws itself."""
        if self.closed:
            return
        try:
            self.pty.resize(rows, max(1, cols - 1))
        except OSError:
            return
        asyncio.get_running_loop().call_later(SHAKE_MS / 1000, self.resize, cols, rows, True)

    # ── Hooks (protocol.md "상태") ────────────────────
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
            new = HOOK_STATUS.get(ev)      # Notification and unknown events change nothing
            if new:
                self.status = new
            # A hook arriving down this path belongs to that agent. SessionStart's http hook was once seen
            # not to arrive (spike E, once), so we do not lean on SessionStart alone — weak evidence is enough here.
            self.agent = agent
        if (self.status, self.agent, self.last_event) != before:
            registry.changed(self)

    def seen(self) -> None:
        """The browser saw it. done → idle only — waiting does not clear just by being looked at."""
        if self.status == "done":
            self.status = "idle"
            registry.changed(self)
        elif self.status == "unknown" and self.derived == "done":   # done lit by the title (#38)
            self.derived = "idle"
            registry.changed(self)

    # ── Death ───────────────────────────────────────
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
        if self.writing:                     # remove add_writer before closing the fd (#1)
            try:
                asyncio.get_running_loop().remove_writer(self.pty.fileno())
            except Exception:
                pass                         # blocking ptys never set `writing`, so this is POSIX only
            self.writing = False
        # Salvage what the shell emitted just before dying (an exit echo, say). **Within the budget only** —
        # SIGHUP has not been sent yet, so the child can still write here, and then when this loop ends is
        # up to the child's speed. The cap is here for the same reason as PUMP_BUDGET in `_on_readable`.
        # **Not unmeasured — measured, and it did not show up**: DELETEing a pane running `yes` on macOS,
        # even the old budget-less loop ended by itself at 2048 bytes with EAGAIN (2026-09-08, once). Linux
        # was not measured. So this did not fix an observed hang; it put a cap where there was none.
        # **Only where reading can say "nothing right now".** This loop ends because a non-blocking
        # read raises BlockingIOError once it is drained. On Windows ReadFile simply waits, and a
        # pane that has stopped printing waits forever — measured 2026-09-14: the probe's six-minute
        # job timeout, with nothing after "a pane prints something". Draining a blocking handle needs
        # the thread that step 2 introduces; until then the salvage is skipped there, and what is
        # lost is an exit echo, not correctness.
        drained = 0
        while not self.pty.blocking and drained < PUMP_BUDGET:
            try:
                chunk = self.pty.read(65536)
            except OSError:
                break
            if not chunk:
                break
            self.pending += chunk
            drained += len(chunk)
        if self.pending:
            self._emit(bytes(self.pending))
            self.pending.clear()
        self.pty.close()
        # **Wake the reader so it can see `closed` and leave.** close() cancels the read it is sitting
        # in (CancelIoEx); this releases one that is parked on the Event instead. Without it the
        # thread outlives the pane — daemon, so it would not hold a shutdown, but it would hold the
        # pane object and its ring buffer for as long as the daemon lives.
        self._can_read.set()
        self.pty.hangup()
        asyncio.get_running_loop().call_later(KILL_GRACE_S, reaper.kill_if_alive, self)
        for a in list(self.attached):
            try:
                a.writer.write(Frame.close())
            except Exception:
                pass
            self.detach(a)
        try:
            self.settings_path.unlink()          # when a pane dies, delete <id>.json
        except OSError:
            pass
        # Clear the ring buffer (256KB per pane) and the queues here. This session leaves the registry in a
        # moment with no way to attach again, so there is nothing to replay, and **the daemon never restarts**
        # (⑦=b), so we do not rely on when references drop — the reaper holds this object until SIGCHLD reaps the pid.
        self.ring.segs.clear()
        self.ring.size = 0
        self.pending.clear()
        self.inq.clear()
        self.carry = b""
        registry.gone(self)


# ── Child-exit detection ──────────────────────────────────────────────────────────────
class Reaper:
    """On SIGCHLD, waitpid(WNOHANG) on every pid we know. Signals can coalesce, so never look at just one.

    **There is no SIGCHLD on Windows.** A pane's death is learned there the other way round: the read
    path gets EOF from the console and calls `die("pty eof")`, which is a path both platforms already
    take — Linux reports a dead child as EIO and macOS as empty bytes, so nothing here is new, it is
    just the *only* route rather than the second one. What is lost is the grace period's backstop
    (`kill_if_alive`), and the job object is what replaces it: ConPty.close tears down the whole tree
    (#29, docs/windows.md)."""

    def __init__(self):
        self.pids: dict[int, Session] = {}

    def install(self, loop) -> None:
        if not hasattr(signal, "SIGCHLD"):
            log("no SIGCHLD on this platform — a pane's death is the console's EOF")
            return
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
        """SIGKILL if the grace after SIGHUP (KILL_GRACE_S) has passed and it still has not been reaped.
        **Compares the session object, not the pid** — if that pid is reaped and reused during the grace,
        `pid in self.pids` becomes true again, and what dies then is someone else's (the new session's) shell."""
        if self.pids.get(s.pid) is s:
            # **Through the pty, not os.kill.** signal.SIGKILL does not exist on Windows, and this runs
            # from a call_later that `die` always schedules — so it was an AttributeError in a callback
            # on every closed pane there (found by the source scan, 2026-09-14). The seam already has
            # the right answer on both sides: a signal on POSIX, TerminateJobObject on Windows, which
            # takes the whole tree rather than the shell alone.
            s.pty.kill()


# ── Canvases (protocol.md "캔버스", ⑪ ⑫) ──────────────────────────────────────────
class Canvas:
    """One tab. A session always belongs to exactly one canvas.

    The daemon holds only id, name and order — no coordinates, no minimap, no "the tab in view"
    (protocol.md "없는 것"). The id has the **same shape** as a session id. Nothing is authorized
    by a canvas id (it opens neither a hook URL nor /pty); they match to keep one id shape. seq is used by neither.
    """
    __slots__ = ("id", "name", "order", "seq")

    def __init__(self, cid: str, name=None, seq: int = 1):
        self.id = cid
        self.name = name
        self.order = 0      # the registry renumbers by position — from 0 with no gaps, lower is further left
        # Which one it was created as. **The label of an unnamed canvas is built from this** — build it from
        # order and the labels swap the moment a tab is dragged elsewhere, which to the user looks like a
        # canvas renaming itself (user report 2026-09-08). Positions move; this number does not.
        # Delete one and make another and the numbering skips — that is right. It is a placeholder, not a rank.
        self.seq = seq

    def to_json(self) -> dict:
        return {"id": self.id, "name": self.name, "order": self.order, "seq": self.seq}


# ── Session registry + the /events broadcast ──────────────────────────────────────────
class Registry:
    def __init__(self):
        self.sessions: dict[str, Session] = {}
        #: What happened (up to LOG_MAX entries, oldest first). It outlives the session — which is why the
        #: name is pinned in **as the value it had then**, not as a reference.
        self.log: collections.deque = collections.deque(maxlen=LOG_MAX)
        #: Exactly the order shown in the tab strip. Python dicts keep insertion order, so this is order —
        #: order is never sorted separately, so there is nowhere for "from 0 with no gaps" to break.
        self.canvases: dict[str, Canvas] = {}
        #: Creation sequence. **Never reused** — hand a deleted number out again and labels collide again.
        self.canvas_seq = 0
        self.event_clients: set = set()
        #: ③ — the board: {session id: {x, y, w, h, z, f?, g?}}. Read from LAYOUT_FILE at start-up,
        #: replaced whole by PUT /api/layout, broadcast as {"t": "layout"} so every other browser follows.
        self.layout: dict = {}
        self.layout_rev = 0           # climbs on every save, so a browser can drop a stale broadcast

    # ── Sessions ─────────────────────────────────────────
    def list(self) -> list[Session]:
        return sorted(self.sessions.values(), key=lambda s: s.created)

    def create(self, cwd: str, canvas=None, name=None) -> Session:
        sid = secrets.token_urlsafe(16)          # 22 chars, unguessable. No sequence numbers (#29)
        # Unknown canvas (or none given) → the default canvas. A session does not exist without one (⑪).
        cid = canvas if canvas in self.canvases else self.default_canvas().id
        write_pane_settings(sid)                 # must exist before the shell starts, so a claude typed at once finds it
        s = Session(sid, cwd, 80, 24, cid, name)
        self.sessions[sid] = s
        reaper.watch(s)
        s.start_reading()
        self.note(s, "created", "opened")
        self.changed(s)
        log(f"session {sid} created  cwd={cwd} canvas={cid} pid={s.pid}")
        return s

    # ── Canvases ─────────────────────────────────────────
    def canvas_list(self) -> list:
        return list(self.canvases.values())      # insertion order = order

    def default_canvas(self) -> Canvas:
        """The canvas lowest in order. **There is never a moment without a canvas** — one is made at start-up."""
        if not self.canvases:                    # defensive. On the normal path one exists from start-up
            self.canvas_changed(self.new_canvas())
        return next(iter(self.canvases.values()))

    def _renumber(self) -> None:
        for i, c in enumerate(self.canvases.values()):
            c.order = i

    def new_canvas(self, name=None) -> Canvas:
        """**Appended at the end** — existing canvases keep their order, so one canvas frame broadcasts it."""
        self.canvas_seq += 1
        c = Canvas(secrets.token_urlsafe(16), name, self.canvas_seq)
        self.canvases[c.id] = c
        self._renumber()
        return c

    def reorder_canvases(self, ids: list) -> None:
        """**All** of what exists now, in a new order. The caller checks first that this is an exact permutation.
        Why there is no way to change one at a time is in protocol.md "캔버스" — an intermediate state
        leaves two browsers drawing different tab strips."""
        self.canvases = {i: self.canvases[i] for i in ids}
        self._renumber()

    def drop_canvas(self, cid: str) -> None:
        self.canvases.pop(cid, None)
        self._renumber()                         # renumber what is left from 0, with no gaps again

    def canvas_sessions(self, cid: str) -> list:
        return [s for s in self.sessions.values() if s.canvas == cid]

    def broadcast(self, obj) -> None:
        frame = Frame.text(obj)
        for w in list(self.event_clients):
            if w.is_closing():
                self.event_clients.discard(w)
                continue
            if queue_full(w):
                # One spectator that is not reading must not eat daemon memory. Cut it and the browser reattaches.
                self.event_clients.discard(w)
                try:
                    w.close()
                except Exception:
                    pass
                continue
            try:
                w.write(frame)
            except Exception:
                self.event_clients.discard(w)

    #: Transitions worth writing down. The value is the wording the browser shows as-is.
    LOG_WORTH = {"waiting": "wants you", "done": "finished"}

    def note(self, s: Session, kind: str, what: str) -> None:
        e = {"t": time.time(), "id": s.id, "kind": kind, "what": what,
             "name": s.name, "cwd": s.cwd, "canvas": s.canvas, "agent": s.agent}
        self.log.append(e)
        self.broadcast({"t": "log", "e": e})

    def changed(self, s: Session) -> None:
        """**The session remembers the previous status.** Make the caller pass it in and missing one of the
        seven call sites silently loses that event — so it goes where it cannot be missed."""
        st = s.eff_status()
        if st != s.logged:
            s.status_since = time.time()
            # **A wait that ends with nobody having typed here is not an approval palmar can vouch
            # for.** It might be you in another window, an agent in another pane holding the token,
            # or the command finishing on its own. palmar knows every byte it wrote to this pty
            # (send_input is the only path), so it can tell "you answered" from "something did" — and
            # it should not write down the first when it only saw the second (#14).
            if s.logged == "waiting" and st != "waiting" and s.typed_at <= s.waited_at:
                s.answered_elsewhere = True
                self.note(s, st, "answered — not by you here")
            elif st in self.LOG_WORTH:
                self.note(s, st, self.LOG_WORTH[st])
            if st == "waiting":
                s.waited_at = time.monotonic()   # the moment this wait began, to compare typing against
                s.answered_elsewhere = False     # a fresh wait — no answer yet
            s.logged = st
        self.broadcast({"t": "session", "s": s.to_json()})

    def gone(self, s: Session) -> None:
        self.note(s, "gone", "closed")
        self.sessions.pop(s.id, None)
        self.broadcast({"t": "gone", "id": s.id})

    def canvas_changed(self, c: Canvas) -> None:
        """Created, or renamed. Creation appends at the end, so this one frame is enough."""
        self.broadcast({"t": "canvas", "c": c.to_json()})

    def canvases_changed(self) -> None:
        """The order changed — the whole list in order. Split it into N canvas frames and the receiver draws a wrong strip in between."""
        self.broadcast({"t": "canvases", "cs": [c.to_json() for c in self.canvas_list()]})

    def canvas_gone(self, cid: str) -> None:
        """**The order of the two frames is contract** — canvas_gone must go first so the receiver clears
        the tab state it held under that id before it takes the new order (protocol.md "/events")."""
        self.broadcast({"t": "canvas_gone", "id": cid})
        self.canvases_changed()


registry = Registry()
reaper = Reaper()


# ── Preparing ~/.palmar (protocol.md "뜨기") ─────────────────────────────────────
#: **Whether this platform answers with permission bits at all.** On POSIX the 0700 mode and the
#: owning uid *are* the guarantee. On Windows neither exists in that form: `os.getuid` is absent,
#: `chmod(0o600)` lands as 0o666 (measured, docs/windows.md), and what actually keeps other users out
#: of `%USERPROFILE%\.palmar` is the profile directory's ACL, which Windows sets. Pretending to check
#: mode bits there would print a reassuring line about a guarantee that is not the one in force.
POSIX_PERMS = sys.platform != "win32"


def ensure_private_dir(p: Path) -> None:
    """Creates it 0700. If it exists already it must be mine and must have no group/other write bit (#29).
    A symlink is refused — the shim lives here and comes first on PATH.

    **On Windows the last three checks do not apply and are not faked.** See POSIX_PERMS above: the
    directory still has to be a directory and not a link, because that is what stops somebody
    pointing the shim somewhere else, and that check is real on both."""
    try:
        st = os.lstat(p)
    except FileNotFoundError:
        os.mkdir(p, 0o700)
        st = os.lstat(p)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise SystemExit(f"palmard: {p} must be a directory, not a symlink (#29)")
    if not POSIX_PERMS:
        return
    if st.st_uid != os.getuid():
        raise SystemExit(f"palmard: {p} is not owned by me (#29)")
    if st.st_mode & 0o022:
        raise SystemExit(f"palmard: {p} is writable by group or others — chmod 700 it and try again (#29)")
    if st.st_mode & 0o077:
        os.chmod(p, 0o700)        # something merely readable like 0755 is not refused, it is tightened


#: Reading a pane's **current** directory. The cwd a session was created with is not where the person
#: ended up — they `cd` — and what is worth remembering across a restart is where they ended up.
#: `None` means "cannot ask on this platform"; the caller then falls back to the cwd it opened at,
#: which is a worse answer but never a wrong one. A platform we have not taught this to must not
#: silently report the wrong folder.
#: macOS: proc_pidinfo(PROC_PIDVNODEPATHINFO). struct proc_vnodepathinfo is two vnode_info_path of
#: 1176 bytes each, cdir first, and the path sits at offset 152 inside it (152 + MAXPATHLEN 1024).
#: Measured on this machine: 7.9 µs a call, so 50 panes cost 0.4 ms.
_LIBPROC = [False]
_VPI_SIZE, _VPI_OFF, _VPI_PATH = 2352, 152, 1024


#: **What is running in a pane, by name.** The foreground process group is read already (that is how
#: "nothing is running" is known); its leader's command name is one call further — `proc_name` on
#: macOS, `/proc/<pid>/comm` on Linux — and it is whatever is there: claude, vim, npm, anything. Not a
#: list of known agents (user, 2026-09-15: "하드코딩 말고"). Windows has no foreground group, so it
#: stays None there until the shim writes the name into the title (#30).
def comm_of(pid: int):
    if sys.platform == "darwin":
        if _LIBPROC[0] is False:
            cwd_of(pid)                      # loads libproc, or records that it cannot
        if not _LIBPROC[0]:
            return None
        ctypes, lib = _LIBPROC[0]
        try:
            if not hasattr(lib.proc_name, "_set"):
                lib.proc_name.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
                lib.proc_name.restype = ctypes.c_int
                lib.proc_name._set = True
            buf = ctypes.create_string_buffer(256)
            n = lib.proc_name(pid, buf, 256)
            return buf.raw[:n].decode("utf-8", "replace") or None if n > 0 else None
        except Exception:
            return None
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/%d/comm" % pid, "rb") as fh:
                return fh.read().strip().decode("utf-8", "replace") or None
        except OSError:
            return None
    return None


#: **A version is not a name.** Claude Code installs itself as `~/.local/share/claude/versions/2.1.278`
#: with a symlink called `claude` pointing at it, so the program that actually runs is named after its
#: version and the rail said `2.1.274` where it meant `claude` (user, 2026-09-20). Anything installed
#: that way reads the same, which is why this asks the shape of the name rather than knowing about one
#: program: a bare version number is not something to show a person.
VERSIONISH = re.compile(r"^v?\d+(\.\d+){1,3}([-+.][0-9A-Za-z.\-+]*)?$")
#: Directory names that say where a thing is kept rather than what it is. Walking up from the binary,
#: these are stepped over on the way to the first component that means something.
PLACE_DIRS = frozenset({"versions", "version", "bin", "sbin", "libexec", "current", "latest",
                        "releases", "release", "dist", "build", "lib", "node_modules", ".bin",
                        "contents", "macos", "resources", "usr", "local", "opt", "share"})


def name_from_path(path):
    """The first component of a path that is worth showing, from the end backwards."""
    for part in reversed([p for p in re.split(r"[\\/]+", path or "") if p]):
        if VERSIONISH.match(part) or part.lower() in PLACE_DIRS:
            continue
        return part
    return None


#: Where a process runs from. **Only asked when the name is a version** — it costs a syscall and a
#: buffer, and every other name is already the answer.
def exe_of(pid: int):
    if sys.platform == "darwin":
        if _LIBPROC[0] is False:
            cwd_of(pid)                      # loads libproc, or records that it cannot
        if not _LIBPROC[0]:
            return None
        ctypes, lib = _LIBPROC[0]
        try:
            buf = ctypes.create_string_buffer(4096)
            n = lib.proc_pidpath(pid, buf, 4096)
            return buf.raw[:n].decode("utf-8", "replace") if n > 0 else None
        except Exception:
            return None
    if sys.platform.startswith("linux"):
        try:
            return os.readlink("/proc/%d/exe" % pid)
        except OSError:
            return None
    return None


def cwd_of(pid: int):
    if sys.platform == "darwin":
        if _LIBPROC[0] is False:
            try:
                import ctypes, ctypes.util
                lib = ctypes.CDLL(ctypes.util.find_library("proc") or "/usr/lib/libproc.dylib")
                lib.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
                                             ctypes.c_void_p, ctypes.c_int]
                lib.proc_pidinfo.restype = ctypes.c_int
                _LIBPROC[0] = (ctypes, lib)
            except Exception as e:
                log(f"cannot read a pane's directory on this machine — {e}")
                _LIBPROC[0] = None
        if _LIBPROC[0] is None:
            return None
        ctypes, lib = _LIBPROC[0]
        buf = ctypes.create_string_buffer(_VPI_SIZE)
        if lib.proc_pidinfo(pid, 9, 0, buf, _VPI_SIZE) <= 0:      # 9 = PROC_PIDVNODEPATHINFO
            return None
        raw = buf.raw[_VPI_OFF:_VPI_OFF + _VPI_PATH].split(b"\0", 1)[0]
        return raw.decode("utf-8", "replace") or None
    if sys.platform.startswith("linux"):
        try:
            return os.readlink("/proc/%d/cwd" % pid)
        except OSError:
            return None
    if sys.platform == "win32":
        return _cwd_of_win(pid)
    return None


#: Windows has no /proc and nothing like proc_pidinfo. A process's current directory lives in its own
#: memory — PEB → RTL_USER_PROCESS_PARAMETERS → CurrentDirectory — and reading it needs
#: PROCESS_VM_READ, which you have for your own processes and nobody else's. That is the same
#: boundary as everything else here (README, "what palmar does not protect you from").
#:
#: **The offsets are the fragile part.** They are stable for x64 across Windows 10 and 11 and widely
#: relied on, but they are not contract — so every step is checked and any failure means `None`,
#: which is the answer this function has always had for a platform it cannot ask. Falling back to the
#: folder a pane was opened in is a worse answer, never a wrong one.
#:
#: **And it cannot follow a PowerShell `cd`, however correctly it reads.** `Set-Location` moves
#: PowerShell's *own* location and never calls SetCurrentDirectory, so the process working directory
#: — which is what the PEB holds — stays where the shell started. Confirmed by the shape of the
#: report: four panes, all cd-ed, all still reading as their opening folder (user, 2026-09-14).
#: `cmd.exe` does update it, and so does any program that chdir()s, so this is right for those and
#: silent for PowerShell rather than wrong. **Following a PowerShell cd needs the shell to say so** —
#: a prompt that writes the path into the window title, which palmar already watches. That is the
#: Windows half of the PATH shim, and it is not built (#29, docs/windows.md).
_NTDLL = [False]
_PEB_PROCESS_PARAMETERS = 0x20          # PEB.ProcessParameters, x64
_RUPP_CURRENT_DIRECTORY = 0x38          # RTL_USER_PROCESS_PARAMETERS.CurrentDirectory.DosPath, x64


def _cwd_of_win(pid: int):
    import ctypes
    from ctypes import wintypes
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        return None                     # 32-bit Python cannot read a 64-bit process's PEB
    if _NTDLL[0] is False:
        try:
            nt = ctypes.WinDLL("ntdll", use_last_error=True)
            nt.NtQueryInformationProcess.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                     wintypes.ULONG, ctypes.POINTER(wintypes.ULONG)]
            nt.NtQueryInformationProcess.restype = ctypes.c_long
            _NTDLL[0] = nt
        except Exception:
            _NTDLL[0] = None
    nt = _NTDLL[0]
    if not nt:
        return None
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                      ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    k32.ReadProcessMemory.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL

    PROCESS_QUERY_INFORMATION, PROCESS_VM_READ = 0x0400, 0x0010
    h = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not h:
        return None
    try:
        def at(addr, size):
            buf = (ctypes.c_ubyte * size)()
            n = ctypes.c_size_t(0)
            if not k32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, size, ctypes.byref(n)):
                return None
            return bytes(buf[:n.value]) if n.value == size else None

        # PROCESS_BASIC_INFORMATION: the PEB address is the second pointer-sized field.
        pbi = (ctypes.c_ubyte * 48)()
        got = wintypes.ULONG(0)
        if nt.NtQueryInformationProcess(h, 0, pbi, 48, ctypes.byref(got)) != 0:
            return None
        peb = int.from_bytes(bytes(pbi[8:16]), "little")
        if not peb:
            return None
        raw = at(peb + _PEB_PROCESS_PARAMETERS, 8)
        if not raw:
            return None
        params = int.from_bytes(raw, "little")
        if not params:
            return None
        # UNICODE_STRING: Length, MaximumLength (USHORT each), then the buffer pointer.
        us = at(params + _RUPP_CURRENT_DIRECTORY, 16)
        if not us:
            return None
        length = int.from_bytes(us[0:2], "little")
        buf_at = int.from_bytes(us[8:16], "little")
        if not length or not buf_at or length > 4096:
            return None
        text = at(buf_at, length)
        if not text:
            return None
        out = text.decode("utf-16-le", "replace").rstrip("\\")
        return out or None
    except Exception:
        return None
    finally:
        k32.CloseHandle(h)


def _write_then_replace(path: Path, data: bytes, mode: int) -> None:
    """A fresh temp file beside `path` — a name nobody could have planted — then rename over it.

    **The temp name used to be fixed** (`<name>.palmar-tmp`), and the folder is a cloned repository's
    to fill: a FIFO by that name blocked the open until somebody read it (the daemon is one thread —
    everything stopped), a hard link by that name was truncated in place, taking the file it linked
    to with it. mkstemp is O_CREAT|O_EXCL on a random name: it makes a new file or fails, never opens
    what was there (Codex review, 2026-09-15)."""
    import tempfile
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix="." + path.name + ".", suffix=".palmar-tmp")
    try:
        if POSIX_PERMS:
            os.fchmod(fd, mode)   # a no-op on Windows anyway; see POSIX_PERMS
        os.write(fd, data)
        os.close(fd)
        fd = -1
        os.replace(tmp, path)
    except BaseException:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_atomic(path: Path, data: bytes) -> float:
    """Write beside it and rename over it, so the file is never half-written — and keep the mode it had.
    Returns the new mtime, which is the stamp the next save has to match."""
    try:
        mode = path.stat().st_mode & 0o777
    except OSError:
        mode = 0o644
    _write_then_replace(path, data, mode)
    return path.stat().st_mtime


def write_private(path: Path, data: bytes, mode: int) -> None:
    """Writes anew with mode. Writes a temp file and renames — never leaves a running shim half-written."""
    _write_then_replace(path, data, mode)


def write_pane_settings(sid: str) -> None:
    """~/.palmar/run/<id>.json per pane (0600). All six events, one and the same http hook."""
    url = f"http://127.0.0.1:{PORT[0]}/hook/claude?pane={sid}&token={TOKEN[0]}"
    hooks = {ev: [{"hooks": [{"type": "http", "url": url}]}] for ev in HOOK_EVENTS}
    write_private(RUN_DIR / f"{sid}.json", json.dumps({"hooks": hooks}, indent=1).encode() + b"\n", 0o600)


#: The last snapshot written, so an unchanged one is not rewritten. cwd is polled, and polling
#: something that rarely changes should not mean writing a file every time it is looked at.
_LAST_SNAP = [None]


def snapshot() -> dict:
    """What is worth having back after this daemon is gone: the canvases, and each pane's name and
    **the folder it is in now** — not the one it opened at.

    **It asks the panes to look first.** This used to read `cwd_of(pid)` here and hand the answer
    straight to the file, which was right for the file and left the browser with the folder the pane
    opened in for the life of the session. Now one call answers both: each pane samples itself, and
    this reads what the panes say. Sampling **here** rather than only on the ten-second tick is what
    keeps a clean stop honest — it saves on the way out, seconds after a `cd`, and the tick would not
    have come round yet (measured 2026-09-17: the first version of this lost exactly that case)."""
    for x in registry.list():
        try:
            x.sample_cwd()
        except Exception:
            pass
    return {
        "v": 1,
        "canvases": [{"id": c.id, "name": c.name} for c in registry.canvas_list()],
        "sessions": [{"name": s.name, "canvas": s.canvas,
                      "cwd": s.cwd}          # kept live by sample_cwd / the title marker
                     for s in registry.list()],
    }


_ID_RE = re.compile(r"[A-Za-z0-9_\-:]{1,80}")   # session ids, and "v:<hash>" for a viewer window


def clean_layout(obj):
    """The board as the daemon will keep it, or None if this is not a board. Only the fields the page
    writes, only as numbers (bool is an int in Python and is refused), only ids shaped like ids — the
    file is served back to every browser, so what goes in is what comes out."""
    if not isinstance(obj, dict) or len(obj) > LAYOUT_MAX_ENTRIES:
        return None
    out = {}
    for sid, r in obj.items():
        if not isinstance(sid, str) or not _ID_RE.fullmatch(sid) or not isinstance(r, dict):
            return None
        e = {}
        for k in ("x", "y", "w", "h", "z", "f"):
            v = r.get(k)
            if v is None:
                continue
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                return None
            e[k] = v
        g = r.get("g")
        if g is not None:
            if not isinstance(g, str) or len(g) > 64:
                return None
            e["g"] = g
        # **A group's name rides on its members, not in a table of its own.** A table would need
        # pruning — a name whose group has lost every window is an orphan nobody sweeps up — and this
        # way the name goes when the last member does, exactly as `g` already does. Every member
        # carries the same string; the page writes them all and reads the first that has one.
        gn = r.get("gn")
        if gn is not None:
            if not isinstance(gn, str) or len(gn) > 64:
                return None
            e["gn"] = gn
        # A viewer window is not a session: the page keeps it on the board by its kind and path, and
        # brings it back from there (2026-09-15). Strings, bounded, nothing else.
        for k, cap in (("kind", 16), ("path", 4096), ("canvas", 64)):
            v = r.get(k)
            if v is not None:
                if not isinstance(v, str) or len(v) > cap:
                    return None
                e[k] = v
        out[sid] = e
    return out


def read_settings() -> dict:
    """What the person turned on, or nothing. A file that cannot be read is worth less than none."""
    try:
        d = json.loads(SETTINGS_FILE.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def save_settings() -> None:
    try:
        write_private(SETTINGS_FILE, json.dumps(SETTINGS[0]).encode() + b"\n", 0o600)
    except OSError as e:
        log(f"could not write {SETTINGS_FILE.name} — {e}")


def update_check_on(env=None, settings=None) -> bool:
    """Is the release check on? The file says, and `$PALMAR_UPDATE_CHECK` overrides it either way —
    `0` off, anything else on. A locked-down machine needs a way that does not go through a screen,
    and a scripted one needs a way that does not need a click."""
    env = os.environ if env is None else env
    settings = SETTINGS[0] if settings is None else settings
    chosen = env.get("PALMAR_UPDATE_CHECK")
    if chosen is not None:
        return chosen.strip() not in ("", "0", "no", "off")
    return settings.get("update_check") is True


def version_tuple(v: str):
    """`v0.1.2` or `0.1.2` → `(0, 1, 2)`. None for anything else — a tag we do not understand must
    not come out looking newer than what is installed."""
    m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)$", (v or "").strip())
    return tuple(int(g) for g in m.groups()) if m else None


def is_newer(latest: str, mine: str) -> bool:
    a, b = version_tuple(latest), version_tuple(mine)
    return bool(a and b and a > b)


def tag_from_latest_url(final: str) -> str:
    """The tag `…/releases/latest` redirected to, or ''. Matched rather than trusted: what comes back
    decides what a page is told, and an address that is not a release tag says nothing."""
    m = re.match(r"^https?://[\w.:-]{1,80}/[\w.-]{1,64}/[\w.-]{1,64}/releases/tag/([\w.+-]{1,64})$",
                 (final or "").strip())
    return m.group(1) if m else ""


def fetch_latest(url: str = None, timeout: float = 10.0, opener=None) -> str:
    """The newest released version, or ''. **One HEAD**, following the redirect, and the version is
    read off the address it lands on — no page is downloaded and nothing but the tag is learned.

    Blocking on purpose: it is called through an executor so the loop never waits on the network."""
    url = url or os.environ.get("PALMAR_LATEST_URL") or LATEST_URL
    opener = urllib.request.urlopen if opener is None else opener
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "palmar/" + __version__})
    try:
        with opener(req, timeout=timeout) as r:
            final = r.geturl()
    except Exception as e:
        log(f"the release check did not get through ({e})")
        return ""
    return tag_from_latest_url(final).lstrip("v")


def update_state() -> dict:
    """What a page is told about the release check: whether it is on, and the newest version seen
    when that is newer than this one. `latest` is null when there is nothing to say."""
    latest = SETTINGS[0].get("latest")
    newer = isinstance(latest, str) and is_newer(latest, __version__)
    return {"on": update_check_on(), "latest": latest if newer else None, "url": LATEST_URL}


async def update_tick(loop) -> None:
    """Ask once and tell the open pages if there is something newer."""
    if not update_check_on():
        return
    latest = await loop.run_in_executor(None, fetch_latest)
    SETTINGS[0]["checked_at"] = time.time()
    if latest:
        SETTINGS[0]["latest"] = latest
    save_settings()
    if latest and is_newer(latest, __version__):
        log(f"palmar {latest} is out — this is {__version__} ({LATEST_URL})")
    registry.broadcast(dict(update_state(), t="update"))


def arm_update_check(loop, first: float = None) -> None:
    """(Re)arm the release check, or take it down. **Off means no timer at all** — a daemon nobody
    asked opens nothing and wakes for nothing. Called at start-up and whenever the switch moves."""
    if UPDATE_TIMER[0] is not None:
        UPDATE_TIMER[0].cancel()
        UPDATE_TIMER[0] = None
    if not update_check_on():
        return

    def tick():
        loop.create_task(update_tick(loop))
        UPDATE_TIMER[0] = loop.call_later(UPDATE_EVERY_S, tick)

    due = UPDATE_FIRST_S if first is None else first
    # A daemon that came back inside the day does not ask again just because it came back.
    try:
        since = time.time() - float(SETTINGS[0].get("checked_at") or 0)
    except (TypeError, ValueError):
        since = UPDATE_EVERY_S
    if 0 <= since < UPDATE_EVERY_S:
        due = max(due, UPDATE_EVERY_S - since)
    UPDATE_TIMER[0] = loop.call_later(due, tick)


def read_layout() -> dict:
    """What the last daemon kept, or nothing. A file that cannot be trusted is worth less than none."""
    try:
        d = json.loads(LAYOUT_FILE.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(d, dict) or d.get("v") != 1:
        return {}
    return clean_layout(d.get("layout")) or {}


def save_layout() -> None:
    try:
        write_private(LAYOUT_FILE, json.dumps({"v": 1, "layout": registry.layout}).encode() + b"\n", 0o600)
    except OSError as e:
        log(f"could not write {LAYOUT_FILE} — {e}")


def save_restore() -> None:
    """Write the recovery file, but only when it would say something new."""
    try:
        snap = snapshot()
    except Exception as e:
        log(f"could not take a restore snapshot — {e}")
        return
    if snap == _LAST_SNAP[0]:
        return
    _LAST_SNAP[0] = snap
    body = dict(snap)
    body["saved"] = time.time()
    try:
        write_private(RESTORE_FILE, json.dumps(body).encode() + b"\n", 0o600)
    except OSError as e:
        log(f"could not write {RESTORE_FILE} — {e}")


def read_restore():
    """The file left by the previous daemon, or None. Anything malformed is None — a recovery file
    that cannot be trusted is worth less than no recovery file."""
    try:
        d = json.loads(RESTORE_FILE.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or d.get("v") != 1:
        return None
    cs = d.get("canvases")
    ss = d.get("sessions")
    if not isinstance(cs, list) or not isinstance(ss, list):
        return None
    return d


def restore_offer():
    """The sessions the previous daemon left, or None once there is nothing to offer.

    It stops being offered the moment a pane exists — restoring "your eight from before" next to
    panes you have already opened would double them, and the person plainly moved on."""
    d = RESTORE[0]
    if not d or registry.sessions:
        return None
    ss = [x for x in d.get("sessions") or []
          if isinstance(x, dict) and isinstance(x.get("cwd"), str)]
    if not ss:
        return None
    return {"saved": d.get("saved"), "sessions": ss}


class AlreadyRunning(Exception):
    """A palmard already has this HOME, and it answers. Carries its address (key and all).

    **Not a failure.** The web page and the app (app/) are two views of one daemon, so "palmar is
    already up" is the answer to `palmar`, not an error — show the person that one."""

    def __init__(self, url: str):
        super().__init__(url)
        self.url = url


def daemon_answers(url: str) -> bool:
    """Is something listening at that address? The flock says a process is alive; this says the
    address we are about to hand over actually reaches it."""
    try:
        host, _, port = url.split("//", 1)[1].split("/", 1)[0].rpartition(":")
        with socket.create_connection((host, int(port)), timeout=0.5):
            return True
    except (OSError, ValueError, IndexError):
        return False


def _read_lock_line(fd):
    """The pid line out of run/lock. `(text, None)` or `(None, why)`.

    **A daemon from before 2026-09-14 locks byte 0, and that lock is mandatory on Windows** — so the
    one byte the pid line starts with cannot be read while it runs, and `--stop` was refused by the
    daemon it was trying to stop. Its lock covers exactly one byte, so everything after it still
    reads: `id 1234 http://…` is enough to find the pid, and the leading `p` is put back.

    This is tolerance for one old build, not a format. Newer daemons lock far past any content and
    the first read simply works."""
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        return os.read(fd, 256).decode("utf-8", "replace"), None
    except OSError as e:
        first = e
    try:
        os.lseek(fd, 1, os.SEEK_SET)
        rest = os.read(fd, 256).decode("utf-8", "replace")
    except OSError:
        return None, first
    if rest.startswith("id "):
        return "p" + rest, None
    return None, first


def _ask_to_stop(url: str) -> bool:
    """`POST /api/stop` on a running daemon. True if it took the request.

    The token comes from run/token, which is 0600 — the same file every other local tool reads. If it
    is not there, or the daemon is older than this route, this simply fails and the caller signals."""
    try:
        token = TOKEN_FILE.read_text("utf-8").strip()
    except OSError:
        return False
    base = url.split("/?")[0]
    req = urllib.request.Request(base + "/api/stop?token=" + token, data=b"",
                                 headers={"Origin": base}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


#: Set once the loop is up; `POST /api/stop` calls it. A list so the route can reach it without a global.
STOP_NOW = [None]


def stop_daemon() -> int:
    """`palmar --stop` — stop the daemon that has this HOME, and its shells with it.

    **A daemon that answers is running, whatever the lock says.** The lock used to be the only
    authority here, and it is a good one — held by a live daemon for exactly as long as it lives, where
    a pid can be stale or reused. But it is indirect: a daemon from an older build holds a *different*
    byte, and `--stop` then took the lock, concluded nothing was running, and said so while the daemon
    went on serving (user, 2026-09-14). The address answering is direct evidence, and `app/` has always
    trusted it over the file. So the lock decides only when nothing answers.

    SIGTERM, not SIGKILL — the daemon has a shutdown path and that is where the restore file gets
    written (protocol.md). Killing it outright would throw away the workspace it is about to save.
    **On Windows SIGTERM is TerminateProcess**, which is a SIGKILL by another name, so a console
    control event goes instead: the daemon is started in its own process group for exactly that."""
    path = RUN_DIR / "lock"
    # Ask the address first. It is the one check that cannot be fooled by which byte a build locks.
    try:
        said = URL_FILE.read_text("utf-8").strip()
    except OSError:
        said = ""
    answering = said.startswith("http://") and daemon_answers(said)
    try:
        fd = os.open(str(path), os.O_RDWR | NOFOLLOW | BINARY)
    except FileNotFoundError:
        print("palmar: no daemon is running" + ("" if not answering else
              f" (something else answers at {said.split('/?')[0]} — not palmar's)"))
        return 0
    except OSError as e:
        print(f"palmar: could not open run/lock — {e}")
        return 1
    try:
        try:
            lock_fd(fd)
        except OSError:
            pass                                    # held — a daemon is alive, which is the point
        else:
            # **A free lock is the answer.** A live daemon holds it for exactly as long as it lives,
            # and run/url is removed when it stops or when a new one starts — so a free lock with an
            # address that answers is a squatter on the old port, or a file a kill -9 left behind.
            # This used to send that listener the token and SIGTERM whatever pid the stale lock line
            # named (review, 2026-09-15).
            unlock_fd(fd)
            print("palmar: no daemon is running" + ("" if not answering else
                  f" (something else answers at {said.split('/?')[0]} — not palmar's)"))
            return 0
        raw, why = _read_lock_line(fd)
        if raw is None:
            print(f"palmar: could not read run/lock — {why}")
            if answering:
                print(f"        but {said.split('/?')[0]} answers, so a daemon is running.")
                if sys.platform == "win32":
                    print("        end that python:  Get-Process python | Stop-Process")
                else:
                    print("        end that process:  pkill -f 'python.*-m palmar'")
            return 1
        # "pid 1234 http://127.0.0.1:8801" — the address is there for --doctor; only the pid matters here.
        line = raw.split()
        if len(line) < 2 or line[0] != "pid" or not line[1].isdigit():
            print(f"palmar: run/lock says nothing it can read ({' '.join(line)[:60]})")
            return 1
        pid = int(line[1])
        # **Ask over the socket first.** A console event cannot reach a daemon that has no console,
        # and a detached one on Windows has none — GenerateConsoleCtrlEvent has nowhere to send it and
        # os.kill falls back to TerminateProcess, which skips the shutdown and the restore snapshot
        # (user, 2026-09-14, after I assumed the process group would be enough). The socket is there
        # on every platform, it is already authenticated, and it ends in the same place Ctrl-C does.
        if answering and _ask_to_stop(said):
            print(f"palmar: asked pid {pid} to stop; waiting…")
        else:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                    print("palmar: that daemon is already gone")
                    return 0
            except PermissionError:
                print(f"palmar: cannot signal pid {pid} — it belongs to another user")
                return 1
            print(f"palmar: asked pid {pid} to stop; waiting…")
    finally:
        os.close(fd)
    # Gone means the lock is free again. Poll rather than waitpid — it is not our child.
    end = time.monotonic() + STOP_WAIT_S
    while time.monotonic() < end:
        time.sleep(0.1)
        try:
            fd = os.open(str(path), os.O_RDWR | NOFOLLOW | BINARY)
        except OSError:
            print("palmar: stopped")
            return 0
        try:
            lock_fd(fd)
        except OSError:
            continue
        else:
            unlock_fd(fd)
            print("palmar: stopped")
            return 0
        finally:
            os.close(fd)
    print(f"palmar: it did not stop within {STOP_WAIT_S:.0f}s. Try again, or as a last resort `kill -9 {pid}`")
    return 1


def acquire_single_instance_lock() -> None:
    """An exclusive flock on ~/.palmar/run/lock. Failing to take it means another palmard already uses this HOME —
    a second start would write a new token below and delete every run/*.json, silently dropping the first one's pane
    hooks (#2, violating AGENTS principle 3 'notice it and say so'). So only the lock holder does that.

    **Without it we do not refuse any more** (2026-09-11): the daemon is one per HOME by design, and with
    a web page *and* an app both in use, running into a daemon that is already up is the ordinary case, not
    a mistake. If it answers, `AlreadyRunning` carries its address up and the caller opens that. The refusal
    below is kept for the case where something holds the lock and cannot be reached — that really is wrong."""
    fd = os.open(str(RUN_DIR / "lock"), os.O_RDWR | os.O_CREAT | NOFOLLOW | BINARY, 0o600)
    try:
        lock_fd(fd)
    except OSError:
        try:
            prev = os.read(fd, 256).decode("utf-8", "replace").strip()
        except OSError:
            prev = ""
        os.close(fd)
        # run/url is 0600 and holds the address with its key. Reaching it is the whole point here, so
        # unlike the lock file — which never holds the key — this one is read and handed on.
        # The holder removes run/url the moment it takes the lock and writes it back once it is
        # bound (below), so a file that is there is the holder's — and one that is not yet there is a
        # start still in progress: wait a moment for it rather than call a live daemon unreachable.
        running = ""
        for _ in range(30):
            try:
                running = URL_FILE.read_text("utf-8").strip()
            except OSError:
                running = ""
            if running.startswith("http://") and daemon_answers(running):
                raise AlreadyRunning(running)
            time.sleep(0.1)
        # **The key is never written into the lock file** — `--doctor` prints this line as-is, and that
        # output exists to be pasted (#14). So we do not hand out the whole address here either, only where
        # to get it back. Otherwise we would point at a keyless address, and opening that gives 403 (measured).
        raise SystemExit(f"palmard: another palmard holds {PALMAR_DIR} but does not answer"
                         f"{' — ' + prev if prev else ''} (one daemon per HOME)\n"
                         f"        its address: cat {URL_FILE}")
    os.ftruncate(fd, 0)
    os.write(fd, f"pid {os.getpid()} http://127.0.0.1:{PORT[0]}\n".encode())
    LOCK_FH[0] = fd     # kept open while the daemon lives — the lock drops when it closes
    # **A run/url on disk means a live daemon wrote it — nothing older.** It used to outlive the
    # daemon (a clean stop, a reboot), and everything that found it trusted a bare TCP connect to
    # say the address was still ours: any local account could bind the old port and be handed the
    # key by the window, by a second `palmar`, by the Windows start (review, 2026-09-15). Gone here,
    # the moment the lock is ours; written back only once this daemon is bound.
    try:
        URL_FILE.unlink()
    except FileNotFoundError:
        pass
    except OSError as e:
        log(f"could not remove the old run/url — {e}")


KEY_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


def load_or_make_key() -> str:
    """The right to be handed the page. **Used as-is if it is there; made anew if absent or broken.**

    Unlike the token it is not rotated at every start. Rotating it changes the address every time and kills
    the bookmark, and a bookmark that keeps working is what the user chose. The token is still minted anew
    every time — that is how an old page notices the daemon restarted (protocol.md 'authentication')."""
    had = KEY_FILE.exists()
    try:
        got = KEY_FILE.read_text("utf-8").strip()
    except FileNotFoundError:
        got = ""            # the first start on this HOME. Not worth a line that reads like a fault.
    except (OSError, ValueError, UnicodeDecodeError) as e:
        log(f"could not read run/key ({e}) — making a new one")
        got = ""
    # Check the length and the characters. Trust a hand-edited or half-written file and characters that
    # cannot go in a URL slip in, leaving a daemon nobody can open.
    if len(got) >= 32 and set(got) <= KEY_CHARS:
        # **Permissions are tightened every time.** Write 0600 only at creation and a file loosened somehow
        # (restore, copy, umask) gets used as-is. The token file is rewritten 0600 at every start-up; only this one was not.
        try:
            if KEY_FILE.stat().st_mode & 0o077:
                os.chmod(KEY_FILE, 0o600)
                log(f"tightened run/key to 0600")
        except OSError as e:
            log(f"could not fix the mode of run/key — {e}")
        return got
    if had:
        # **Never swapped out silently.** A changed key kills the bookmark, and that is exactly what this
        # scheme promised the user (#14). Change it, but say why it changed.
        log("run/key is empty or malformed — making a new one. **The address changes** (take the bookmark again)")
    key = secrets.token_urlsafe(32)
    write_private(KEY_FILE, key.encode() + b"\n", 0o600)
    return key


def setup_palmar_dir() -> str:
    """In order. If any one of them fails, it does not start."""
    for d in (PALMAR_DIR, BIN_DIR, RUN_DIR):
        ensure_private_dir(d)
    acquire_single_instance_lock()   # only the daemon holding the lock rotates the token and deletes *.json (#2)
    KEY[0] = load_or_make_key()      # inside the lock — so two cannot make one and overwrite each other
    token = secrets.token_urlsafe(32)
    write_private(TOKEN_FILE, token.encode() + b"\n", 0o600)
    # **Not on Windows.** It is a `#!/bin/sh` script under an extension-less name: no shebang there,
    # and PATHEXT does not list "" — so it is a file that can never be run, sitting first on PATH.
    # The preamble below is what stands in its place.
    if POSIX_PERMS:
        write_private(BIN_DIR / "claude", SHIM.encode(), 0o755)
    else:
        ensure_private_dir(PWSH_DIR)
        # Doubled, because it lands inside a single-quoted PowerShell string — see pwsh_argv.
        write_private(PWSH_FILE,
                      PWSH_PREAMBLE.replace("{run}", str(RUN_DIR).replace("'", "''")).encode(), 0o600)
    ensure_private_dir(ZDOT_DIR)
    ensure_private_dir(BASHRC.parent)
    write_private(BASHRC, BASH_RC.encode(), 0o600)
    for name, body in ((".zshenv", ZSHENV), (".zprofile", ZPROFILE),
                       (".zshrc", ZSHRC), (".zlogin", ZLOGIN)):
        write_private(ZDOT_DIR / name, body.encode(), 0o600)
    # Orphan <id>.json — the previous daemon's panes are gone now (⑦=b, no handoff).
    for f in RUN_DIR.glob("*.json"):
        try:
            f.unlink()
        except OSError:
            pass
    return token


# ── Directories (protocol.md /api/dirs) ───────────────────────────────────────────────
def owned_by_me(p) -> bool:
    """Is the resolve()d path owned by the current uid? The one predicate that splits the roots (#31).
    stat follows symlinks — the caller passes in something already resolve()d.

    **Windows has no uid.** The honest answer there is "I cannot tell", and the caller's roots are
    already confined to the profile, so this says True rather than refusing every directory. It is
    a weaker statement on that platform and this line is where that is written down."""
    if not POSIX_PERMS:
        return True
    try:
        return os.stat(str(p)).st_uid == os.getuid()
    except OSError:
        return False


def tops() -> list[str]:
    """The top of the machine, to browse down from. **Not a root** — `roots()` is still the floor
    `under_roots` checks, so appearing here does not make a place one a terminal can open in.

    POSIX has one top and it is `/`. Windows has one per drive, and `/` there means "the root of
    whichever drive happens to be current", which is both wrong and unstable — so the directory rail
    came up **empty** on Windows (user, 2026-09-14). Drives are probed rather than listed because
    `os.listdrives` is 3.12 and the floor here is 3.9."""
    if sys.platform != "win32":
        return ["/"]
    out = []
    for c in "CDEFGHIJKLMNOPQRSTUVWXYZAB":
        d = "%s:\\" % c
        if os.path.isdir(d):
            out.append(d)
    return out or ["C:\\"]


def roots() -> list[Path]:
    """The user's home + /Users/* and /home/* **that I own**. Re-read per request — two scandirs, cheap.

    Someone else's home is not a root (#31). On WSL, running as `aa`, `/home/bb` showed up in the directory
    rail and a shell could be opened there — a Linux home is usually drwxr-xr-x, so R_OK|X_OK alone lets all
    of them through. The shell still runs as `aa`, so it is no privilege escalation, but it is someone else's
    place, and above all **roots are the floor of the cwd check** (`under_roots`): the wider they are, the
    more `POST /api/sessions` accepts.
    So `$HOME` always goes in and the rest **only when I own them** — someone with several homes sees all of
    theirs. Symlinks are resolved first (as before): a link to someone else's home is measured against it.

    On macOS this rule catches `/Users/Shared` (root-owned, drwxrwxrwt) and drops it — confirmed by measurement.
    Not showing someone else's things was judged worth more. One line here puts it back."""
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
                            continue                    # someone else's home — not a root (#31)
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
    """p is already resolve()d. It must be a root or under one. No startswith (#29)."""
    return any(p.is_relative_to(r) for r in roots())


#: Max bytes read from `.git` and `HEAD`. One branch line and one `gitdir:` line never need more.
META_MAX = 4096


#: **Zero on Windows, and that is not a gap.** The flag is here because a `.git/HEAD` that is a FIFO
#: makes `open()` wait for a writer and stops the whole daemon, not one pane. Windows has no FIFOs in
#: the filesystem namespace — named pipes live under `\\.\pipe\` and cannot appear inside a folder —
#: so there is nothing there for it to defend against. The other two defences below are not
#: platform-specific and still run: the file must be regular, and only META_MAX bytes are read.
#: It was `os.O_NONBLOCK` inline, which does not exist there — so **every** directory entry raised
#: AttributeError and the rail came up empty (found with `--doctor`, 2026-09-14).
NONBLOCK = getattr(os, "O_NONBLOCK", 0)


def read_meta(p: Path) -> str:
    """Reads one piece of folder metadata **without ever blocking**. Empty string if it cannot be read.

    A plain `open()` will not do. This is not a folder the user picked but **someone else's folder passed
    over while drawing a list on screen**, so we do not get to choose what is inside it:
      · if `.git/HEAD` is a FIFO, `open()` waits for a writer and **stands there forever.** That is not one
        pane but **the whole daemon** standing still — list drawing runs on the event loop, so other panes'
        bytes, the `/events` broadcast and every request stop too (measured 2026-09-09: timed 4s, blocked all 4s).
      · pointed at `/dev/zero`, no newline ever comes and memory grows with every read.
    So all three are applied: open with `O_NONBLOCK`, check **after** opening that it really is a regular
    file (check before opening and it can change in between), and cap how much is read.
    """
    fd = None
    try:
        fd = os.open(str(p), os.O_RDONLY | NONBLOCK)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return ""                       # FIFO, device, socket — not ours to read
        return os.read(fd, META_MAX).decode("utf-8", "replace")
    except OSError:
        return ""
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass


def git_branch(d: Path):
    """One line of <dir>/.git/HEAD. If .git is a file (worktree) it follows gitdir:. git is never run.
    On a detached HEAD, the short hash (7 chars) — not a branch name, but it must not read as 'not a repo'."""
    try:
        g = d / ".git"
        if g.is_file():
            first = read_meta(g).split("\n", 1)[0].strip()
            if not first.startswith("gitdir:"):
                return None
            gd = Path(first[len("gitdir:"):].strip())
            head = (gd if gd.is_absolute() else d / gd) / "HEAD"
        else:
            head = g / "HEAD"
        line = read_meta(head).split("\n", 1)[0].strip()
    except OSError:
        return None
    if line.startswith("ref: "):
        ref = line[5:]
        return ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
    return line[:7] or None


def has_entries(p: str) -> bool:
    """Is there anything inside that does not start with a dot? Stops at the first.

    **Folders used to be the only thing that counted**, because the rail showed folders only. Now that it
    shows files too, a folder holding nothing but files answered "empty" and drew no caret, so there was
    no way to see it had anything in it (user, 2026-09-15)."""
    try:
        with os.scandir(p) as it:
            for e in it:
                if not e.name.startswith("."):
                    return True
    except OSError:
        pass
    return False


def dir_entry(name: str, path: str) -> dict:
    return {"name": name, "kind": "dir", "git_branch": git_branch(Path(path)), "has_children": has_entries(path)}


def list_dirs(path: Path) -> list[dict]:
    """Folders, then files — dot-prefixed ones skipped, each by name. Reads this one folder only — it does
    not walk a tree. Files came in on 2026-09-15 when the rail stopped being a launcher and became the
    way to look at a document (user); a file entry carries `kind: "file"` and its size, a folder
    `kind: "dir"`, so the page can tell them apart without guessing from the name.
    Runs synchronously inside the event loop (120-250ms of blocking on 5000 folders, #7). It is not moved to
    run_in_executor because that starts a thread, and with a thread present a later pty.fork risks deadlock
    (that is why palmard is single-threaded — the comment at the top of this file, and AGENTS). Ordinary
    folders are fine, and it runs once when a person unfolds one, so #7 is left unfixed and written down here."""
    entries = []
    files = []
    with os.scandir(path) as it:
        for e in it:
            if e.name.startswith("."):
                continue
            try:
                if not e.is_dir(follow_symlinks=True):
                    if e.is_file(follow_symlinks=True):
                        row = {"name": e.name, "kind": "file", "size": e.stat(follow_symlinks=True).st_size}
                        if e.is_symlink():
                            row["link"] = True          # the page can say so; PUT refuses it either way
                        files.append(row)
                    continue
            except OSError:
                continue
            # Metadata for every child. The 2026-09-09 rule read it only inside the roots — to keep a
            # folder outside them from leaking its branch through a link — but that rested on "you
            # cannot reach outside the roots", which the 2026-09-11 decision reversed: browsing is
            # allowed anywhere, opening is not. Once you can list `/etc` directly, hiding the branch
            # of one folder under it is inconsistent, not protective. The daemon-freeze guard that
            # matters (a FIFO at .git/HEAD) lives in read_meta and is untouched by this.
            entries.append(dir_entry(e.name, e.path))
    entries.sort(key=lambda x: x["name"].casefold())
    files.sort(key=lambda x: x["name"].casefold())
    # A cap, not a budget: a folder of a hundred thousand names would otherwise be stat-ed whole and
    # shipped whole, and nobody reads that.
    return (entries + files)[:LIST_MAX]


LIST_MAX = 4000        # names per folder in one listing — see list_dirs


#: Folder labels. (when it was built, or None, [paths…]) — used only by `find_dirs`.
#: **Absent is None, not 0.0.** The reference point of `time.monotonic()` is up to the platform — on this Mac
#: it starts at 0 when the process starts, so using 0.0 for "never built" means that for the daemon's first
#: 60 seconds `now - 0.0 > TTL` is false and no labels get built at all (measured 2026-09-08: 0 hits right after start).
_FIND_INDEX: list = [None, []]
_FIND_LOCK: list = [None]


async def build_find_index() -> list:
    """Sweeps the folders under the roots once. **Yields to the loop every `FIND_YIELD` entries** — without
    that, every pane's bytes stop for the length of the sweep (204ms measured)."""
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


#: **Finding the file somebody dragged in from Finder or Explorer.**
#:
#: A page is never told where a dropped file is. `text/uri-list` carries a `file://` URL on macOS and
#: is simply absent on Windows (user, 2026-09-18), and `dataTransfer.files` gives a name, a size, a
#: modification time and the bytes — never a location. The bytes are what an upload wants; palmar
#: wants the file **where it is**, because a viewer here is a window onto the file on disk and saves
#: back to it. So the three facts that did come over are used to find it.
#:
#: **Only an exact name, and only where palmar is already looking.** The first version searched the
#: roots and stopped there, which is narrower than palmar's own rule for *reading* a file: the roots
#: are the floor for things that **act** on the machine — opening a shell, saving an edit — while
#: `/api/file` reads anywhere this uid can read, because looking is looking. A file on a work drive
#: could be opened by walking to it in the rail and not by dropping it, which is a difference with no
#: reason behind it (user, 2026-09-18: "home 아래에 없으면 안열려?").
#:
#: So the search also covers **the folders the files already open came from.** A pane's own directory
#: would have been the obvious thing to add and adds nothing: a pane cannot be opened outside the
#: roots in the first place (measured — `POST /api/sessions` refuses it), so every one of them is
#: already covered. A **viewer** is not floored that way, because reading is not acting: walk to a
#: work drive in the rail, open a file, and that folder is somewhere this person opens files from.
#: From then on a drop out of it finds its way home.
#:
#: **And the other drives.** A home on C: and the file on D: is the ordinary shape of a Windows
#: machine, and the rail already walks there and opens it — so a drop that could not was the same
#: inconsistency one level out (user, 2026-09-18). Every attached volume is a top: drive letters on
#: Windows, `/Volumes` on a Mac, the usual mount points on Linux.
#:
#: **Except the ones with no end worth waiting for.** A network drive is skipped, by asking the
#: system what kind it is rather than by guessing from the name. And every top gets its own budget of
#: entries so a wide home cannot starve the drive the file is actually on, with a deadline over the
#: whole search, because this runs while somebody watches a window not open yet. What is found when
#: the time is up is what is returned; the page says it looked and did not find it, and the rail
#: still reaches anywhere.
FIND_FILE_SECONDS = 4.0
#:
#: Size and modification time are matched by the caller; two files agreeing on all three is not
#: something to guess between, and the page then opens neither and says so.
#:
#: Deeper than the folder index (that one labels places to open a terminal; this one looks for one
#: file) and capped both ways, with the same yield so a sweep does not stop every pane's bytes.
FIND_FILE_DEPTH = 8
FIND_FILE_HITS = 24
FIND_FILE_NODES = 120_000


#: Every volume that is attached and is not somebody else's machine. **The kind is asked, not
#: guessed**: a mapped network drive looks exactly like a local one by its letter, and sweeping one is
#: how a search stops being a search. Anything that cannot be asked is left out.
def volume_tops(platform=None, bases=None) -> list:
    platform = sys.platform if platform is None else platform
    out = []
    if platform == "win32":
        try:
            import ctypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            mask = k32.GetLogicalDrives()
            for i in range(26):
                if not (mask >> i) & 1:
                    continue
                d = "%s:\\" % chr(ord("A") + i)
                # 2 removable · 3 fixed · 6 RAM disk. 4 is a network drive and 5 a disc.
                if k32.GetDriveTypeW(ctypes.c_wchar_p(d)) in (2, 3, 6):
                    out.append(d)
        except Exception:
            return []
        return out
    if bases is None:
        bases = ["/Volumes"] if platform == "darwin" else ["/media", "/mnt", "/run/media"]
    for base in bases:
        try:
            with os.scandir(base) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False) and not e.name.startswith("."):
                            out.append(e.path)
                    except OSError:
                        continue
        except OSError:
            continue
    return out


def search_tops() -> list:
    """The roots, the folder of every file window on the board, and every attached volume — with
    anything already covered by an earlier top dropped, so a home and a folder inside it are swept
    once, and in that order because that is how often each one holds the answer."""
    tops = [str(r) for r in roots()]

    def add(c):
        if not c:
            return
        if any(c == t or c.startswith(t.rstrip(os.sep) + os.sep) for t in tops):
            return
        try:
            if os.path.isdir(c):
                tops.append(c)
        except OSError:
            pass

    for r in (registry.layout or {}).values():
        if isinstance(r, dict) and r.get("kind") == "file" and isinstance(r.get("path"), str):
            add(os.path.dirname(r["path"]))
    for v in volume_tops():
        add(v)
    return tops


async def find_files(name: str) -> list:
    out: list = []
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        return out
    want = name.lower()
    tops = search_tops()
    # **Every top gets a turn.** With one deadline over the whole search, a home big enough to spend it
    # is a home that hides every other drive — the budget in entries already guards against that and
    # the clock did not (user, 2026-09-18: a file on D: with the home on C:, still not found). So the
    # time is shared out as well, with a floor so a machine with many volumes does not give each one
    # too little to be worth starting.
    share = max(0.6, FIND_FILE_SECONDS / max(1, len(tops)))
    deadline = time.monotonic() + FIND_FILE_SECONDS
    # **It stops at the nearest place that has an answer.** The tops are in the order each one holds
    # the answer — home, then the folders files were opened from, then the other drives — and going on
    # past a hit means sweeping an external disk to ask whether your own home is lying to you. The
    # caller's rule against guessing between identical files still holds where it matters: within the
    # place the file was found. Only a name that is nowhere pays for the whole search.
    for root in tops:
        if out or time.monotonic() > deadline:
            break
        n = 0                                   # its own budget: a wide home cannot starve a drive
        until = min(deadline, time.monotonic() + share)
        stack = [(str(root), 0)]
        while stack and len(out) < FIND_FILE_HITS and n < FIND_FILE_NODES:
            d, depth = stack.pop()
            try:
                with os.scandir(d) as it:
                    for e in it:
                        n += 1
                        if n % FIND_YIELD == 0:
                            await asyncio.sleep(0)
                            if time.monotonic() > until:
                                n = FIND_FILE_NODES     # this top's turn is over; the next one begins
                                break
                        if n >= FIND_FILE_NODES:
                            break
                        if e.name.startswith(".") or e.name in FIND_SKIP or e.name.endswith(FIND_SKIP_SUFFIX):
                            continue
                        try:
                            if e.is_dir(follow_symlinks=False):
                                if depth + 1 < FIND_FILE_DEPTH:
                                    stack.append((e.path, depth + 1))
                                continue
                            if e.name.lower() != want or not e.is_file(follow_symlinks=False):
                                continue
                            st = e.stat()
                        except OSError:
                            continue
                        out.append({"path": e.path, "size": st.st_size, "mtime": st.st_mtime})
                        if len(out) >= FIND_FILE_HITS:
                            break
            except OSError:
                continue
    return out


async def find_dirs(qs: str) -> list:
    """Searches the labels. **A path typed out in full comes first** — pasting a path you already know is
    the most common use, and it can be answered even when it is not in the labels (outside the depth)."""
    hits: list = []
    seen = set()

    exact = resolve_under_roots(os.path.expanduser(qs)) if qs.startswith(("/", "~")) else None
    if exact is not None and exact.is_dir():
        hits.append(dir_entry(str(exact), str(exact)))
        seen.add(str(exact))

    now = time.monotonic()
    if _FIND_INDEX[0] is None or now - _FIND_INDEX[0] > FIND_TTL_S:
        if _FIND_LOCK[0] is None:            # many asking at once still sweeps only once
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
    # **Nearest first.** Exact name match → name starts with it → name contains it → path contains it.
    # Within a rank, shallower first — `~/work/api` is more often the one wanted than `~/…/…/…/apiservice`.
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


#: **Looking at a document.** A file is served back as it is: images as images, text as text, and
#: nothing else — a file with a NUL in its first bytes is "not text" (415), one over FILE_MAX is "too
#: big" (413). Read-only; there is no PUT. The boundary is the one browsing has (resolve_dir): anywhere
#: this uid can read — the same files the shell beside it can `cat`, so nothing new is reachable.
#: **A big file is shown from the top, not refused.** Most real files were over the old 2 MB and the
#: viewer just said no (user, 2026-09-15). Text goes out as its first TEXT_HEAD bytes with the total in
#: a header, and the page says "the first 4 MB of 48"; an image or a PDF goes whole up to BLOB_MAX,
#: because the browser draws those and half a picture is nothing. A truncated file is read-only —
#: saving a head over a whole would destroy the rest.
FILE_MAX = 8 * 1024 * 1024            # the most a PUT may carry
TEXT_HEAD = 4 * 1024 * 1024           # of a text file, the part that is shown
BLOB_MAX = 64 * 1024 * 1024           # an image or a document, whole or not at all
#: **.svg is not in here on purpose.** It is a document that can carry script, and the viewer hands what it
#: gets to a blob: URL — "open image in new tab" would then run that script in palmar's own origin (review,
#: 2026-09-15). An SVG is served as its source, which is also the more useful thing to see.
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
               ".webp": "image/webp", ".bmp": "image/bmp", ".ico": "image/x-icon"}
#: A PDF goes out as a PDF so the browser's own viewer draws it — no library is vendored for this
#: (2026-09-15). **HTML does not**: served as text/html from this origin, a page opened in a tab would
#: run its script beside palmar's own; the viewer renders it in a sandboxed frame from text instead.
DOC_TYPES = {".pdf": "application/pdf"}
#: Writing is narrower than reading, and deliberately: plain text and Markdown (user, 2026-09-15
#: "편집은 텍스트 파일이나 md 파일만"). Everything else is read-only — the shell beside it is where a
#: binary gets changed. An extension not on this list is refused rather than guessed at.
EDITABLE = {".txt", ".md", ".markdown", ".text", ".log", ".csv", ".tsv", ".json", ".yaml", ".yml",
            ".toml", ".ini", ".cfg", ".conf", ".env", ".sh", ".bash", ".zsh", ".ps1", ".py", ".js",
            ".ts", ".jsx", ".tsx", ".css", ".html", ".htm", ".xml", ".sql", ".rs", ".go", ".c", ".h",
            ".cpp", ".hpp", ".java", ".rb", ".php", ".lua", ".vim", ".gitignore", ".dockerignore"}


def editable(f: Path) -> bool:
    """Plain text by its name. A file with no suffix (Makefile, LICENSE) counts — those are text by
    convention and refusing them would be pedantic; one with a suffix nobody writes by hand does not."""
    return f.suffix.lower() in EDITABLE or not f.suffix


#: What the system opener runs rather than shows. POSIX: anything with an execute bit, plus the
#: bundles and launchers it runs without one. Windows: the script and shortcut types WSH and the
#: shell execute.
RUNS_POSIX = frozenset({".command", ".tool", ".terminal", ".app", ".desktop", ".pkg", ".jar", ".run"})
RUNS_WIN = frozenset({".bat", ".cmd", ".exe", ".com", ".msi", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh",
                      ".ps1", ".lnk", ".scr", ".pif", ".hta", ".url", ".reg", ".jar"})


def would_run(target: Path, platform=None, mode=None) -> bool:
    """Would the system opener execute this file instead of showing it?"""
    platform = sys.platform if platform is None else platform
    suffix = target.suffix.lower()
    if platform == "win32":
        return suffix in RUNS_WIN
    if mode is None:
        try:
            mode = target.stat().st_mode
        except OSError:
            return True
    return bool(mode & 0o111) or suffix in RUNS_POSIX


def through_a_link(raw: str) -> bool:
    """Does this path go through a symlink anywhere below a root? A cloned repository decides where
    its links point — `notes.txt -> ~/.zshrc` — and a save to what looks like a repo file would land
    on the target (review, 2026-09-15). Links above the roots (`/var` on a Mac) are the system's."""
    p = Path(raw)
    cur = Path(p.anchor) if p.anchor else Path(".")
    inside = False
    for part in p.parts[1:] if p.anchor else p.parts:
        cur = cur / part
        if inside and cur.is_symlink():
            return True
        if not inside:
            try:
                inside = under_roots(cur.resolve())
            except (OSError, RuntimeError):
                inside = False
            if inside and cur.is_symlink():
                return True
    return False


def resolve_file(raw) -> Path | None:
    """Absolute path string → resolve() → an existing regular file, or None. No root check — browsing's rule."""
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        return None
    p = Path(raw)
    if not p.is_absolute():
        return None
    try:
        p = p.resolve()
        return p if p.is_file() else None
    except (OSError, RuntimeError):
        return None


def resolve_dir(raw) -> Path | None:
    """Absolute path string → resolve() → an existing directory, or None. **No root check.**

    This is for *browsing* (`GET /api/dirs?path=`), which the user asked to reach anywhere on the
    machine (2026-09-11): the tree opens from `/`, not just from home. Reading a folder's names is
    not the same as being able to work there — opening a terminal still goes through
    `resolve_under_roots` below, which keeps the cwd inside the roots. So the rule is: look anywhere,
    open only under a root."""
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
    return p


def resolve_under_roots(raw) -> Path | None:
    """Like resolve_dir, but the result must be under a root — the check that gates **opening** a
    terminal (`POST /api/sessions`). Roots are the floor of the cwd check; browsing is looser."""
    p = resolve_dir(raw)
    return p if (p is not None and under_roots(p)) else None


# ── HTTP ─────────────────────────────────────────────────────────────────────
def http(status: int, body: bytes = b"", ctype: str = "application/json; charset=utf-8",
         head_only: bool = False, extra: str = "") -> bytes:
    # The body of a 4xx is {"error": "one human-readable line"} (protocol.md, under the HTTP table). A 4xx with
    # no body is filled with the reason phrase here — the browser toasts it as-is, so no paths, no internals.
    if status >= 400 and not body:
        body = json.dumps({"error": REASONS.get(status, "Error")}).encode()
    # frame-ancestors 'none' + X-Frame-Options: DENY — so nobody else's page can wrap the palmar UI in an
    # iframe for clickjacking or keystroke bait (#10). A HEAD reply is headers only, but keeps Content-Length (#8).
    head = (f"HTTP/1.1 {status} {REASONS.get(status, 'Unknown')}\r\n"
            f"Content-Length: {len(body)}\r\nCache-Control: no-store\r\nConnection: close\r\n"
            "X-Frame-Options: DENY\r\nContent-Security-Policy: frame-ancestors 'none'\r\n"
            "X-Content-Type-Options: nosniff\r\n")
    if body:
        head += f"Content-Type: {ctype}\r\n"
    head += extra                      # a route's own headers, each already ending in CRLF
    return head.encode() + b"\r\n" + (b"" if head_only else body)


def http_json(status: int, obj) -> bytes:
    return http(status, json.dumps(obj).encode())


def http_error(status: int, msg: str) -> bytes:
    return http_json(status, {"error": msg})


def allowed_origin(headers: dict) -> bool:
    """Origin must be absent (same-origin fetch, curl, a hook) or one we handed out."""
    o = headers.get("origin")
    return o is None or o in (f"http://127.0.0.1:{PORT[0]}", f"http://localhost:{PORT[0]}")


def allowed_host(headers: dict) -> bool:
    """Host, the same two only. Blocks DNS rebinding (attacker domain → 127.0.0.1) from reading the token
    out of index.html. See the protocol.md "인증" section (this check is written up there)."""
    h = headers.get("host")
    return h is None or h in (f"127.0.0.1:{PORT[0]}", f"localhost:{PORT[0]}")


#: Shown when the page is opened without the key. Instead of a dead-end 403, write down **what to do** —
#: whoever lands here is far more likely to be the owner who lost the address than an attacker.
NO_KEY_PAGE = b"""<!doctype html><meta charset="utf-8"><title>palmar</title>
<style>body{font:15px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace;max-width:34rem;
margin:12vh auto;padding:0 1.5rem;color:#2b2723;background:#faf7f2}
code{background:#efe9e0;padding:.15em .4em;border-radius:3px}
@media(prefers-color-scheme:dark){body{color:#e8e2d6;background:#191714}code{background:#2a2621}}</style>
<h1>palmar</h1><p>This address needs the key the daemon printed when it started.</p>
<p>Open the link from the daemon's last line, or get it back with:</p>
<p><code>cat ~/.palmar/run/url</code></p>
<p>The key lives in <code>~/.palmar/run/key</code> and does not change when the daemon restarts,
so a bookmark keeps working.</p>
"""

#: Shown when the address carries a key, and it is not this daemon's. That is not a lost address — it
#: is **another palmar's** address on this port: one started in WSL beside one on Windows, one per
#: user, or a stale bookmark from a HOME that was wiped. Say that, and where the right one is.
WRONG_KEY_PAGE = b"""<!doctype html><meta charset="utf-8"><title>palmar</title>
<style>body{font:15px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace;max-width:34rem;
margin:12vh auto;padding:0 1.5rem;color:#2b2723;background:#faf7f2}
code{background:#efe9e0;padding:.15em .4em;border-radius:3px}
@media(prefers-color-scheme:dark){body{color:#e8e2d6;background:#191714}code{background:#2a2621}}</style>
<h1>palmar</h1><p>The key in this address belongs to a <b>different palmar</b> than the one answering here.</p>
<p>Two of them can meet on one address &mdash; one started in WSL beside one on Windows, or one per user.
The one you meant printed its own address when it started; <code>palmar</code> prints it again, and so does:</p>
<p><code>cat ~/.palmar/run/url</code></p>
<p>If this is the one you meant, its key changed &mdash; open the address it printed, not the bookmark.</p>
"""


def key_ok(q: dict) -> bool:
    """Is `?k=` right? Compared as bytes — `hmac.compare_digest` raises TypeError on a non-ASCII str."""
    return hmac.compare_digest(qget(q, "k", "").encode("utf-8", "surrogatepass"), KEY[0].encode())


def serve_static(path: str, head_only: bool = False, has_key: bool = False, key_given: bool = False) -> bytes:
    name = unquote(path).lstrip("/") or "index.html"
    try:
        f = (WEB / name).resolve()
        ok = f.is_relative_to(WEB) and f.is_file()   # startswith lets a sibling directory through (#29)
    except (OSError, ValueError):
        ok = False
    if ok:
        body = f.read_bytes()
        # **Compared in lower case.** The macOS file system ignores case in names, so `/INDEX.HTML` fetches
        # index.html all the same, but comparing the suffix as-is means `.HTML` misses the `.html` branch
        # below and skips both the gate and the token (measured: macOS 200, Linux 404). No secret leaks, but
        # the same code running differently from machine to machine is what gets made one here.
        suffix = f.suffix.lower()
    elif name == "index.html":
        body, suffix = PLACEHOLDER_INDEX, ".html"
    else:
        return http(404, head_only=head_only)
    if suffix == ".html":
        # **This is the only request that carries the token. That is why the key is asked only here.**
        # The `Origin`/`Host` checks pass anything with **no** header (they are not checks that tell a
        # browser from something else), so this request was handing out the secret without asking for any
        # credential at all: any process on the same machine got the token from one
        # `curl http://127.0.0.1:8801/` and opened a real shell (measured 2026-09-09, #14). A 0600 token file
        # does not help — the same secret went out over a socket. The other static files carry no secret.
        if not has_key:
            return http(403, WRONG_KEY_PAGE if key_given else NO_KEY_PAGE,
                        "text/html; charset=utf-8", head_only=head_only)
        # **Only scripts this daemon serves run in the page, plus this one line.** The page holds the
        # token, and everything it shows — titles, process names, file names, a cloned repository's
        # files — is somebody else's text. Its DOM discipline (textContent, a sandboxed frame) is the
        # first wall; this is the second: an injected <script> is inert without the nonce.
        nonce = secrets.token_urlsafe(16)
        tag = f'<script nonce="{nonce}">window.PALMAR_TOKEN="{TOKEN[0]}"</script>'.encode()
        body = body.replace(b"</head>", tag + b"</head>", 1) if b"</head>" in body else tag + body
        return http(200, body, "text/html; charset=utf-8", head_only=head_only,
                    extra=f"Content-Security-Policy: script-src 'self' 'nonce-{nonce}'; object-src 'none'; base-uri 'none'\r\n")
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
    """Control channel. hello (the whole list) on attach, then session/gone. From the browser, only seen."""
    if not await ws_accept(reader, writer, headers):
        return
    registry.event_clients.add(writer)
    # Referential integrity within one frame — every session.canvas carried here is in the canvases carried with it.
    # `v` is the **protocol** version, not the package version. Once this ships, a cached new page will meet
    # an old daemon — instead of the two not knowing and behaving oddly, let the page say so outright.
    writer.write(Frame.text({"t": "hello", "v": PROTOCOL, "daemon": __version__,
                             # Whether POST /api/notify can reach the OS here — for a window with no
                             # Notification API of its own (palmar's own window on a Mac).
                             "notify": notifier_argv("", "") is not None,
                             # Whether the release check is on, and the newest version if it is
                             # newer than this one. Off by default; the page's switch turns it on.
                             "update": update_state(),
                             "canvases": [c.to_json() for c in registry.canvas_list()],
                             "sessions": [s.to_json() for s in registry.list()],
                             # ③ the board, so the sessions below land where they were left
                             "layout": registry.layout, "layout_rev": registry.layout_rev,
                             # **It comes along at the moment of attaching.** The moment you want to know
                             # "what happened while I was away" is exactly then, so it is not asked twice.
                             "log": list(registry.log),
                             # What the previous daemon left, offered rather than acted on. Dropped once
                             # anything has been opened — an offer to restore beside panes you already
                             # opened is noise, and the workspace it describes is no longer the one you have.
                             "restore": restore_offer()}))
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
    """pane channel. hello + replay (or a shake), then binary = PTY bytes. From the browser, keys, resize, ack."""
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
            if opcode == 0x2:            # binary = keystrokes. Written straight to the PTY
                s.typed_at = time.monotonic()   # a person touched this pane — remember when (#14)
                s.answered_elsewhere = False
                s.send_input(payload)
            elif opcode == 0x1:          # text = control
                try:
                    m = json.loads(payload)
                except ValueError:
                    continue
                if not isinstance(m, dict):
                    continue
                t = m.get("t")
                if t == "resize":         # this and nothing else changes rows and columns
                    s.resize(clamp_int(m.get("cols"), s.cols, 1, MAX_COLS),
                             clamp_int(m.get("rows"), s.rows, 1, MAX_ROWS))
                elif t == "ack":
                    s.ack(a, clamp_int(m.get("n"), 0, 0, 1 << 62))
    finally:
        s.detach(a)


async def handle_request(reader, writer) -> None:
    try:                                          # so an idle or lying connection cannot hold an fd forever (#6)
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

    # ── Authentication — on every request ──
    if not (allowed_origin(headers) and allowed_host(headers)):
        writer.write(http(403))
        return
    # Compared as bytes — hmac.compare_digest raises TypeError on a non-ASCII str (?token=%C3%A9) (#4)
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
                writer.write(http(404))     # protocol.md says nothing about an unknown id — 404 before the upgrade
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
        except asyncio.TimeoutError:                # a connection that never sends its Content-Length (#6)
            writer.write(http(408))
            return

    # ── Hooks — always 200 {} (a hook must exit 0). A wrong token only means the status does not change.
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

    # ── Reading also needs the token ──
    # Up to here a GET on `/api/` walked straight through. The `Origin`/`Host` checks pass anything with
    # **no** header (they cannot stop something that is not a browser — that is not what they are for), so
    # any process on the same machine could read every pane's path with
    # `curl http://127.0.0.1:8801/api/sessions` and sweep the home directory with `/api/dirs` (measured
    # 2026-09-09). The browser already holds the token, so it just attaches it.
    # Blocked **after** the body is fully read — cut it off unread and the next request backs up on a reused connection.
    if path.startswith("/api/") and not token_ok:
        writer.write(http(403))
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
            cwd = HOME if raw is None else resolve_under_roots(raw)   # no cwd means home (spike D's default)
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
                # The daemon has no "canvas currently in view" (two browsers can look at different tabs).
                # With none given, the canvas lowest in order — the default that keeps a one-line curl working.
                cid = registry.default_canvas().id
            elif not isinstance(cid, str) or cid not in registry.canvases:
                writer.write(http_error(400, "unknown canvas"))
                return
            # **Blocked here — before the settings file is written and before the fork.** Block it later
            # and half-built resources are left behind.
            if len(registry.sessions) >= MAX_PANES:
                writer.write(http_error(409, f"too many terminals ({MAX_PANES}) — close one first"))
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
        if not token_ok:                 # renaming and moving canvases are no exception either (protocol.md "인증")
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
        # PATCH — changes **only the keys in the body**. A rename and a canvas move both go out as one
        # session frame: Session holds both, and the receiver already swaps session wholesale.
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
            # The session in the path exists, so an unknown canvas is 400, not 404 (protocol.md HTTP table).
            if not isinstance(cid, str) or cid not in registry.canvases:
                writer.write(http_error(400, "unknown canvas"))
                return
            new_canvas = cid
        if (new_name, new_canvas) != (s.name, s.canvas):
            s.name, s.canvas = new_name, new_canvas
            registry.changed(s)          # the sender gets the broadcast back too — the browser applies it idempotently by id
        writer.write(http_json(200, s.to_json()))
        return

    # ── the address, for moving between the window and a browser ──────────────────────────
    # The window (app/) and the page are two views of one daemon, and someone in one of them
    # reasonably wants the other. The page cannot build the address itself: **it is never given the
    # key** (#14 — the key's job ends when index.html is served), only the token.
    #
    # Handing the key back to a token holder gives away nothing: the token already opens shells, and
    # anything that could take it runs as this user and can read run/key (0600) directly. It is the
    # longer-lived of the two, though, so it goes out only when asked for and the page is told to
    # use it and drop it rather than keep it.
    if path == "/api/address":
        if not token_ok:
            writer.write(http(403))
            return
        if method == "GET":
            writer.write(http_json(200, {"url": f"http://127.0.0.1:{PORT[0]}/?k={KEY[0]}"}))
            return
        writer.write(http(405))
        return
    if path == "/api/focus":
        # `palmar` typed while palmar is open: every page is told to come forward, and the caller is
        # told how many there are — none means it should open one (focus_existing).
        if not token_ok:
            writer.write(http(403))
            return
        if method != "POST":
            writer.write(http(405))
            return
        live = [w for w in registry.event_clients if not w.is_closing()]
        registry.broadcast({"t": "focus"})
        writer.write(http_json(200, {"clients": len(live)}))
        return
    if path == "/api/once":
        # A short-lived address of this daemon's own minting, for a process that is not this daemon —
        # the second `palmar` that opens a running one on Windows or from WSL (launch_target).
        if not token_ok:
            writer.write(http(403))
            return
        if method != "POST":
            writer.write(http(405))
            return
        # Same spelling launch_target mints: the nonce in the query and a host with no dot, so
        # Chromium can keep this window's size across launches (loopback_name).
        writer.write(http_json(200, {"url": f"http://{loopback_name(PORT[0])}:{PORT[0]}/once?n={mint_once()}"}))
        return
    if path == "/api/notify":
        # **The OS says it when the page cannot.** palmar's own window on a Mac (WKWebView) has no
        # Notification API; the page sends the title and body here and the daemon hands them to the
        # OS. 501 when there is no way to on this machine, so the page can say so instead of going quiet.
        if not token_ok:
            writer.write(http(403))
            return
        if method != "POST":
            writer.write(http(405))
            return
        obj = parse_json_body(body) if body else {}
        if not isinstance(obj, dict):
            obj = {}
        title = str(obj.get("title") or "palmar")[:120]
        text = str(obj.get("body") or "")[:400]
        writer.write(http(204) if os_notify(title, text) else http_error(501, "no way to notify on this machine"))
        return
    if path == "/api/address/open":
        if not token_ok:
            writer.write(http(403))
            return
        if method != "POST":
            writer.write(http(405))
            return
        # **The daemon opens it, not the page.** A webview cannot reach the system browser, and on
        # WSL the browser that matters is on the Windows side — which is exactly the walk
        # browser_argv already knows how to make. This way the key never crosses into the page at all.
        # A browser, never the window: this is the window's own button (and the page's).
        ok, _ = open_browser(f"http://127.0.0.1:{PORT[0]}/?k={KEY[0]}")
        writer.write(http(204) if ok else http_error(500, "found no way to open a browser here"))
        return

    if path == "/api/restore":
        # **The daemon does it, so every open browser follows along** — the same rule as everything
        # else that changes state (protocol.md "/events"). Two browsers, one restore.
        offer = restore_offer()
        if method == "POST":
            if offer is None:
                writer.write(http_error(409, "nothing to restore"))
                return
            made = []
            for x in offer["sessions"]:
                cwd = resolve_under_roots(x.get("cwd"))
                if cwd is None:
                    # The folder is gone, or outside the roots now. Open it at home rather than
                    # dropping the pane — losing the name too would make the restore quietly partial.
                    cwd = HOME
                # Through the map, or the pane loses the canvas it was on (see RESTORE_CV).
                cid = RESTORE_CV[0].get(x.get("canvas"))
                try:
                    name = clean_name(x.get("name"))
                except ValueError:
                    name = None
                if len(registry.sessions) >= MAX_PANES:
                    break
                made.append(registry.create(str(cwd), cid, name).to_json())
            RESTORE[0] = None                 # offered once
            log(f"restored {len(made)} pane(s)")
            writer.write(http_json(201, made))
        elif method == "DELETE":
            RESTORE[0] = None
            save_restore()                    # the offer is declined — do not offer it again
            writer.write(http(204))
        else:
            writer.write(http(405))
        return

    if path == "/api/settings":
        # The switches the daemon keeps rather than the browser. One today: the release check.
        if method == "GET":
            writer.write(http_json(200, update_state()))
            return
        if method != "POST":
            writer.write(http(405))
            return
        if not token_ok:
            writer.write(http(403))
            return
        obj = parse_json_body(body)
        if not isinstance(obj, dict) or not isinstance(obj.get("update_check"), bool):
            writer.write(http_error(400, "body must be {\"update_check\": true|false}"))
            return
        SETTINGS[0]["update_check"] = obj["update_check"]
        save_settings()
        # Turned on, ask soon rather than in a minute: somebody just pressed it and is watching.
        # **The answer is update_state(), not what was asked** — $PALMAR_UPDATE_CHECK can overrule
        # the file, and a switch that lies about which way it is pointing is worse than no switch.
        arm_update_check(asyncio.get_running_loop(), first=1.0 if obj["update_check"] else None)
        registry.broadcast(dict(update_state(), t="update"))
        writer.write(http_json(200, update_state()))
        return
    # ③ the board. GET is open like the other reads; PUT replaces it whole — the page owns the object
    # and saves it entire, so a merge would only invent a second author.
    if path == "/api/layout":
        if method == "GET":
            writer.write(http_json(200, {"layout": registry.layout, "rev": registry.layout_rev}))
        elif method == "PUT":
            if not token_ok:
                writer.write(http(403))
                return
            obj = parse_json_body(body)
            cleaned = clean_layout(obj.get("layout")) if obj is not None else None
            if cleaned is None:
                writer.write(http_error(400, "body must be {\"layout\": {id: {x, y, w, h, z, f?, g?}}}"))
                return
            registry.layout = cleaned
            registry.layout_rev += 1
            save_layout()
            by = obj.get("by")
            registry.broadcast({"t": "layout", "layout": cleaned, "rev": registry.layout_rev,
                                "by": by[:64] if isinstance(by, str) else ""})
            writer.write(http_json(200, {"rev": registry.layout_rev}))
        else:
            writer.write(http(405))
        return

    if path == "/api/file":
        if method not in ("GET", "PUT"):
            writer.write(http(405))
            return
        if not token_ok:
            writer.write(http(403))
            return
        f = resolve_file(qget(q, "path"))
        if f is None:
            writer.write(http_error(400, "path must be an absolute file"))
            return
        if method == "PUT":
            # **Writing is narrower than reading.** Reading reaches anywhere this uid can read, because
            # looking is looking; writing stays under the roots, which is the floor everything that
            # *acts* on the machine already uses.
            if not under_roots(f):
                writer.write(http_error(400, "a file outside your home is read-only here"))
                return
            if through_a_link(qget(q, "path", "")):
                writer.write(http_error(400, "that path goes through a link — edit the file it points to"))
                return
            if not editable(f):
                writer.write(http_error(415, "only text files can be edited here"))
                return
            if len(body) > FILE_MAX:
                writer.write(http_error(413, f"too big to save (the limit is {FILE_MAX} bytes)"))
                return
            if b"\x00" in body:
                writer.write(http_error(415, "that is not text"))
                return
            want = qget(q, "mtime", "")
            try:
                now = f.stat().st_mtime
            except OSError as e:
                writer.write(http_error(400, f"cannot read: {e.strerror or e}"))
                return
            # **Refuse rather than win.** Something else may have written it since it was read — an agent
            # in the terminal beside this window is exactly the case. The page asks what to do.
            if want and abs(float(want) - now) > 1e-6:
                writer.write(http_error(409, "it changed on disk since you opened it"))
                return
            try:
                data = write_atomic(f, body)
            except OSError as e:
                writer.write(http_error(400, f"could not save: {e.strerror or e}"))
                return
            writer.write(http_json(200, {"mtime": data}))
            return
        # **Bounded by what is read, not by what stat says.** A procfs file reports 0 bytes and goes on
        # forever — /proc/self/pagemap is 256 GiB of it — so trusting st_size was a way to ask the daemon to
        # read a machine's memory into a string (review, 2026-09-15). One byte past the limit is enough.
        blob = f.suffix.lower() in IMAGE_TYPES or f.suffix.lower() in DOC_TYPES
        cap = BLOB_MAX if blob else TEXT_HEAD
        try:
            with open(f, "rb") as fh:
                data = fh.read(cap + 1)
        except OSError as e:
            writer.write(http_error(400, f"cannot read: {e.strerror or e}"))
            return
        truncated = 0
        if len(data) > cap:
            if blob:
                writer.write(http_error(413, f"too big to show (the limit is {BLOB_MAX // (1024 * 1024)} MB)"))
                return
            try:
                truncated = f.stat().st_size            # the whole, for the page to name
            except OSError:
                truncated = len(data)
            data = data[:cap]
            # Not in the middle of a line: the last partial one is dropped so what is shown is whole lines.
            cut = data.rfind(b"\n")
            if cut > 0:
                data = data[:cut + 1]
        try:
            mtime = f.stat().st_mtime
        except OSError:
            mtime = 0.0
        # **The stamp the writer has to match.** Saving sends it back; if the file moved on in between,
        # the save is refused rather than quietly winning over whatever else wrote it (an agent in the
        # terminal beside it is the case that matters).
        extra = (f"X-Palmar-Mtime: {mtime!r}\r\nX-Palmar-Editable: {'1' if editable(f) and not truncated else '0'}\r\n"
                 + (f"X-Palmar-Truncated: {truncated}\r\n" if truncated else ""))
        ctype = IMAGE_TYPES.get(f.suffix.lower()) or DOC_TYPES.get(f.suffix.lower())
        if ctype is None:
            if b"\x00" in data[:8192]:
                writer.write(http_error(415, "not a text file"))
                return
            # **Text goes out as UTF-8, whatever it was.** A file that is not UTF-8 — CP949 is the one that
            # matters here — came back as replacement characters when it was simply labelled utf-8
            # (review, 2026-09-15). Decoded with the encodings worth trying and re-encoded, it reads.
            for enc in ("utf-8", "cp949", "latin-1"):
                try:
                    data = data.decode(enc).encode("utf-8")
                    break
                except UnicodeDecodeError:
                    continue
            else:
                writer.write(http_error(415, "not a text file"))
                return
            ctype = "text/plain; charset=utf-8"
        writer.write(http(200, data, ctype, extra=extra))
        return

    if path == "/api/open":
        # **Hand it to the machine's own program.** The viewer shows text and images; a spreadsheet or a
        # design file belongs to whatever opens it there (user, 2026-09-15). Under the roots only — the
        # same floor as opening a terminal, because this starts a program on the person's behalf.
        if method != "POST":
            writer.write(http(405))
            return
        if not token_ok:
            writer.write(http(403))
            return
        obj = parse_json_body(body) or {}
        target = resolve_file(obj.get("path"))
        if target is None or not under_roots(target):
            # **Say which of the two it is.** The same 400 covered "that is not a file" and "that file
            # is outside your home", and the second one is a rule somebody chose rather than a mistake
            # somebody made — a person who reads "must be a file" about a file goes looking for a
            # fault that is not there (user chose to keep the limit, 2026-09-18).
            if target is None:
                writer.write(http_error(400, "path must be an absolute file"))
            else:
                writer.write(http_error(400, "opening a file starts a program, and palmar keeps that "
                                             "under your home — this one is outside it"))
            return
        # **Open, never run.** The system opener executes some things instead of showing them: an
        # executable script goes to Terminal on a Mac, .bat/.vbs/.lnk run outright on Windows, a
        # .desktop file's Exec line on some Linux desktops. The file's name and bits come from a
        # cloned repository, so those are refused here (review, 2026-09-15).
        if would_run(target):
            writer.write(http_error(415, "that file would run, not open — use the shell beside it"))
            return
        try:
            if sys.platform == "win32":
                os.startfile(str(target))                      # type: ignore[attr-defined]
            else:
                argv = ["open", str(target)] if sys.platform == "darwin" else ["xdg-open", str(target)]
                subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError as e:
            writer.write(http_error(400, f"could not open it: {e.strerror or e}"))
            return
        log(f"opening {target.name} with the system default program")
        writer.write(http(204))
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

    # **Match /api/canvases/order before /api/canvases/<id>** (protocol.md "캔버스").
    # An id is 22 chars so it cannot collide with "order", but the order is kept so a rewrite does not differ.
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
            # Missing, extra or duplicated. Someone created or deleted a canvas meanwhile, and that browser
            # already has the event, so it can just send again.
            writer.write(http_error(409, "canvas list changed, try again"))
            return
        registry.reorder_canvases(ids)
        registry.canvases_changed()      # one frame — order is a property of the set, so it is not split up
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
            if "name" in obj:            # only the keys in the body change
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
        # DELETE — **empty canvases only, and never the last one** (PROVISIONAL, protocol.md "캔버스").
        # Moving them to the next canvas automatically is out because coordinates are keyed by session id, so
        # a moved window lands on someone else's and push-away runs; and killing the shells on delete breaks
        # principle 2 head-on.
        if registry.canvas_sessions(c.id):
            writer.write(http_error(409, "canvas still has terminals"))
            return
        if len(registry.canvases) <= 1:
            writer.write(http_error(409, "the last canvas cannot be removed"))
            return
        registry.drop_canvas(c.id)
        registry.canvas_gone(c.id)       # canvas_gone → canvases, and that order is contract
        writer.write(http(204))
        return

    if path == "/api/stop":
        # **`palmar --stop`, over the socket the daemon already has.** Signals do not reach a daemon
        # that has no console: on Windows a detached process is in no console at all, so
        # GenerateConsoleCtrlEvent has nowhere to send to and os.kill falls back to TerminateProcess —
        # which skips the shutdown path, and the restore snapshot with it (user, 2026-09-14).
        # This asks it to leave the same way Ctrl-C does, and it leaves by the same door.
        # **The token is the gate**, and the token file is 0600: whoever can read it is already the
        # person who can attach to every pane (README, "what palmar does not protect you from").
        if method != "POST":
            writer.write(http_error(405, "POST"))
            return
        log("asked to stop (POST /api/stop)")
        writer.write(http_json(200, {"stopping": True}))
        await writer.drain()
        STOP_NOW[0] and STOP_NOW[0]()
        return

    if path == "/api/files":
        # Where is the file that was dragged in? See find_files — a name is all a page is given.
        if method != "GET":
            writer.write(http_error(405, "GET"))
            return
        writer.write(http_json(200, {"files": await find_files(qget(q, "name", "").strip())}))
        return

    if path == "/api/dirs":
        if method == "GET":
            find = qget(q, "find", "").strip()
            if find:
                writer.write(http_json(200, {"find": find, "entries": await find_dirs(find)}))
                return
            raw = qget(q, "path")
            if not raw:
                # The root list. path is null and name is an absolute path — the browser uses it as the next path.
                # **`/` leads the list** so the tree can climb to the top of the machine (2026-09-11): it is a
                # place to *browse from*, not a root you can open a terminal in — roots() stays the opening floor.
                # The owned homes still come first-class after it, so the common case is one click, not a climb.
                rs = roots()
                # **Mark which one is home.** The browser used to take the first root as home (to
                # shorten paths to `~`); now that `/` leads the list, home has to be named outright
                # or `/` would render as `~`.
                entries = [dir_entry(t, t) for t in tops()]
                for r in rs:
                    if str(r) == "/":
                        continue
                    e = dir_entry(str(r), str(r))
                    if r == HOME:
                        e["home"] = True
                    entries.append(e)
                writer.write(http_json(200, {"path": None, "entries": entries}))
                return
            # **Browsing is not opening.** Reach any directory on the machine (the user asked for the
            # tree to climb to the top, 2026-09-11); the root check stays on opening a terminal.
            p = resolve_dir(raw)
            if p is None:
                writer.write(http_error(400, "path must be an absolute directory"))
                return
            try:
                entries = list_dirs(p)
            except OSError as e:
                writer.write(http_error(400, f"cannot read: {e.strerror or e}"))
                return
            writer.write(http_json(200, {"path": str(p), "entries": entries}))
        elif method == "POST":
            # Create only — no delete, no rename. The terminal next to it does the rest.
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
    if path == "/once" or path.startswith("/once/"):
        # The short-lived address a command line carried (launch_target): good for its seconds,
        # dead after. Anything else gets a page, not the key.
        #
        # **Two spellings.** `/once?n=…` is what is minted now, so that the path stays the same on
        # every launch and Chromium can keep the window's size (loopback_name). `/once/<nonce>` is
        # still honoured: an address minted by an older daemon, or one already sitting in a browser,
        # has to keep opening.
        if take_once(qget(q, "n", "") if path == "/once" else path[len("/once/"):]):
            writer.write(http(302, b"", extra=f"Location: /?k={KEY[0]}\r\n"))
            return
        writer.write(http(404, b"<!doctype html><meta charset=\"utf-8\"><title>palmar</title>"
                          b"<p style=\"font:15px system-ui;margin:12vh auto;max-width:34rem\">"
                          b"This link to palmar has expired. Run <code>palmar</code> again.</p>",
                          "text/html; charset=utf-8", head_only=(method == "HEAD")))
        return
    if path == "/manifest.webmanifest":
        # **palmar as an installed app, with no binary of ours.** A web app manifest is what lets
        # Edge and Chrome install the page as an app — its own icon, name, Start Menu entry and window
        # with no browser UI ("isn't app mode still a browser?", user, 2026-09-15). start_url holds
        # the key, so this is served only with the key, exactly like index.html (#14) — and the page
        # builds the <link rel=manifest> from its own address, so index.html never carries the key.
        if not key_ok(q):
            writer.write(http(403))
            return
        body = json.dumps({
            # **Who this app is, said out loud.** With no `id`, the browser takes `start_url` as the
            # app's identity — and start_url carries the key. Rotate the key, or come back on another
            # port, and the same palmar installs a second time beside the first, with the shortcut
            # somebody pinned still pointing at the old one. `id` is resolved against the origin and
            # never carries a secret, so it stays the same however the address moves.
            "id": "/",
            "name": "palmar", "short_name": "palmar",
            "description": "Many terminals, one place, and each one keeps where you put it.",
            "start_url": f"/?k={KEY[0]}", "scope": "/", "display": "standalone",
            "launch_handler": {"client_mode": "focus-existing"},   # launched again: the open window, forward
            "background_color": "#f6f3ec", "theme_color": "#1f2329",
            "icons": [{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
                      {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"}],
        }).encode()
        writer.write(http(200, body, "application/manifest+json; charset=utf-8", head_only=(method == "HEAD")))
        return
    writer.write(serve_static(path, head_only=(method == "HEAD"), has_key=key_ok(q),
                              key_given=bool(qget(q, "k", ""))))   # HEAD carries no body (#8)


async def handle(reader, writer) -> None:
    try:
        await handle_request(reader, writer)
        await writer.drain()
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionResetError,
            BrokenPipeError, ValueError):
        pass
    except Exception as e:  # one connection's accident must not kill the daemon — running long is the premise
        log("handler error:", repr(e))
    finally:
        try:
            writer.close()
        except Exception:
            pass


# ── Start-up ─────────────────────────────────────────────────────────────────
def shutdown() -> None:
    """**We close the connections. We do not wait for them to close.**

    Since Python 3.12.1 `Server.wait_closed()` only returns once every open connection and handler is done.
    The browser keeps holding `/events`, so simply waiting means **Ctrl-C does not shut it down; only
    closing the tab does.** Reproduced on 3.13, never seen on 3.9 — the kind of thing you miss writing on
    macOS alone (the user saw it on WSL first)."""
    # **Before anything is torn down.** die() empties the registry, and a snapshot taken after that
    # would faithfully record an empty workspace over the one the person had.
    save_restore()
    writers = [a.writer for s in registry.sessions.values() for a in s.attached]
    for s in list(registry.sessions.values()):
        s.die("daemon stopping")          # on the pane side, die has already sent the close frame
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
    # The address dies with the daemon (see acquire_single_instance_lock). A kill -9 skips this,
    # which is why the lock — not this file — is what the window and the launchers go by.
    try:
        URL_FILE.unlink()
    except OSError:
        pass


# ── opening the page ───────────────────────────────────────────────────────────────────────
# The address carries the key (#14), so "open it for me" is the difference between one command and
# copy-pasting a secret by hand. **The window is not here** — palmar's own window is a separate
# program (app/, wry+tao) that starts this daemon and loads the same address. This is only the
# fallback for when you are running the daemon by hand: it hands the address to a browser.
#
# On WSL without a Linux browser there is no page to show inside Linux, so the address goes out to
# the **Windows** browser (wslview, else powershell/cmd) and reaches back in over WSL2's localhost
# forwarding. `BROWSER` beats everything — the Unix convention, and the seam the tests drive.

#: Anything that can show a page. `xdg-open` is a router, not a browser — on WSL it commonly hands
#: back out to Windows through wslu, which is already the fallback, so it is asked last.
LINUX_BROWSERS = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
                  "microsoft-edge", "microsoft-edge-stable", "firefox")


def wsl_kind(env=None, osrelease=None, wslg=None) -> str:
    """`''` off WSL, `'wsl'` inside it, `'wslg'` when WSL's GUI layer can actually draw a window.

    Every input is injectable on purpose: this is the one thing the Mac it was written on cannot try
    for real, so what it decides still has to be measurable (tests/test_pure.py)."""
    env = os.environ if env is None else env
    if osrelease is None:
        try:
            osrelease = Path("/proc/sys/kernel/osrelease").read_text("utf-8", "replace")
        except OSError:
            osrelease = ""
    low = osrelease.lower()
    # WSL1 and WSL2 both carry "microsoft" in the kernel release. WSL_DISTRO_NAME is WSL's own and is
    # checked as well, because a stripped-down distro can leave that /proc entry unreadable.
    if "microsoft" not in low and "wsl" not in low and not env.get("WSL_DISTRO_NAME"):
        return ""
    if wslg is None:
        wslg = Path("/mnt/wslg").is_dir()
    # The mount alone is not enough. **A display has to be there too** — ssh into a WSL distro and
    # /mnt/wslg is still mounted while there is no screen to put a window on.
    if wslg and (env.get("WAYLAND_DISPLAY") or env.get("DISPLAY")):
        return "wslg"
    return "wsl"


def browser_argv(url: str, *, platform=None, kind=None, env=None, which=None, sys32=None, bundle=None):
    """The command that hands `url` to whatever opens links, or None if this machine offers no way.

    Pure: the platform, the WSL kind, the environment and "is this program here" all arrive as
    arguments, so the choice can be measured on a machine that is none of those things."""
    platform = sys.platform if platform is None else platform
    env = os.environ if env is None else env
    which = shutil.which if which is None else which
    chosen = (env.get("BROWSER") or "").strip()
    if chosen:
        return [chosen, url]
    if platform == "darwin":
        # By bundle id, to the browser that handles http. Plain `open` on the file:// stand-in would
        # go to whatever opens .html files, and on a developer's Mac that is often an editor.
        return ["open", "-b", bundle or default_browser_bundle(), url]
    if platform == "win32":
        # Native Windows, not WSL. `start` is a cmd builtin, so it has to go through cmd — and the
        # empty string is its **title** argument: without it the quoted URL becomes the title and
        # nothing opens, the same trap the WSL branch below already carries a note about.
        return ["cmd", "/c", "start", "", url]
    kind = wsl_kind(env=env) if kind is None else kind
    if kind:
        # A Linux browser is only worth it under WSLg, where there is a screen to draw it on.
        if kind == "wslg":
            for b in LINUX_BROWSERS:
                if which(b):
                    return [b, url]
        if which("wslview"):                     # wslu — the blessed hand-off to the Windows browser
            return ["wslview", url]
        # No wslu. Both of these reach the Windows default browser. **The URL holds the key**, so it
        # lands on a Windows command line where that user's own processes can read it — the same
        # person, so the same boundary argv already is on this side (docs/decisions.md).
        # By name first, then by their full path under System32: the name only resolves when the
        # Windows PATH is appended to the Linux one, and a distro with that off opened nothing
        # (user, 2026-09-15).
        s32 = windows_system32() if sys32 is None else sys32
        ps = ("powershell.exe" if which("powershell.exe")
              else s32 and which(s32 + "/WindowsPowerShell/v1.0/powershell.exe"))
        if ps:
            return [ps, "-NoProfile", "-NonInteractive", "-Command", "Start-Process", url]
        cmd = "cmd.exe" if which("cmd.exe") else s32 and which(s32 + "/cmd.exe")
        if cmd:
            # The empty string is `start`'s title argument. Without it the quoted URL becomes the
            # title and nothing opens — the classic `start "http://…"` bug.
            return [cmd, "/c", "start", "", url]
        return None
    if platform.startswith("linux"):
        for b in LINUX_BROWSERS:
            if which(b):
                return [b, url]
        if which("xdg-open"):
            return ["xdg-open", url]
    return None


def find_app(env=None, which=None, exists=None, kind=None, platform=None) -> str:
    """The path of palmar's own window (app/), or '' when there is none to open here.

    Looked for in this order: `$PALMAR_APP` (a path; `0` or empty means there is none), `palmar-app`
    on PATH (install.sh puts a built one beside the launcher), then a build inside the checkout this
    daemon runs from. The window needs a screen: under WSL that means WSLg, and on Linux a DISPLAY —
    without one the browser is the page, as before. Pure: every input is injectable, so the order is
    measured on a machine that has none of these (tests/test_pure.py)."""
    env = os.environ if env is None else env
    which = shutil.which if which is None else which
    exists = (lambda p: Path(p).is_file()) if exists is None else exists
    platform = sys.platform if platform is None else platform
    chosen = env.get("PALMAR_APP")
    if chosen is not None:
        chosen = chosen.strip()
        if chosen in ("", "0", "no", "off"):
            return ""
        if exists(chosen):
            return chosen
        log(f"PALMAR_APP points at nothing ({chosen}) — looking for the window elsewhere")
    if platform.startswith("linux"):
        kind = wsl_kind(env=env) if kind is None else kind
        if kind == "wsl":
            return ""                    # no screen inside Linux; the Windows browser is the page
        if not (env.get("DISPLAY") or env.get("WAYLAND_DISPLAY")):
            return ""                    # ssh, a container, a bare console
    name = "palmar-app.exe" if platform == "win32" else "palmar-app"
    hit = which(name)
    if hit:
        return hit
    root = WEB.parent.parent
    for rel in ("app/target/universal/" + name, "app/target/release/" + name):
        if exists(str(root / rel)):
            return str(root / rel)
    return ""


#: How long the window gets to fall over before it is trusted. A palmar-app on a Linux without the
#: webkit library dies within a few hundred ms and says so on stderr; a live one is still up.
APP_GRACE_S = 0.8

#: Chromium-family browsers take `--app=`: the page in a window with no tabs and no address bar. That
#: is a window without a binary of ours — "does Windows really need an exe?" (user, 2026-09-15). No:
#: Windows always has an Edge. Firefox has no such mode, so it is not here.


#: The Chromium-family browsers app mode can use, by short name, and where each one lives.
#: `names` is what they are called on a Linux PATH; `win` is the path under a Program Files (or
#: LOCALAPPDATA) root; `mac` the bundle's executable. Order is the fallback order when the person's
#: default browser is not one of them: Chrome first because installing it was a choice, Edge last
#: because every Windows has one.
CHROMIUMS = (
    ("chrome",   ("google-chrome", "google-chrome-stable"),   r"Google\Chrome\Application\chrome.exe",
                 "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    ("chromium", ("chromium", "chromium-browser"),            r"Chromium\Application\chrome.exe",
                 "/Applications/Chromium.app/Contents/MacOS/Chromium"),
    ("brave",    ("brave-browser", "brave"),                  r"BraveSoftware\Brave-Browser\Application\brave.exe",
                 "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"),
    ("edge",     ("microsoft-edge", "microsoft-edge-stable"), r"Microsoft\Edge\Application\msedge.exe",
                 "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
)


def chromiums_in_order(prefer: str = ""):
    """CHROMIUMS with the person's default browser first, when it is one of them. Edge opened on a
    machine whose browser is Chrome, and the person asked where Chrome's app mode was (2026-09-15)."""
    rows = list(CHROMIUMS)
    rows.sort(key=lambda r: 0 if r[0] == prefer else 1)
    return rows


def chromium_candidates(platform, env, kind="", cdrive="", prefer=""):
    """Where a Chromium-family browser sits when it is not on PATH; from WSL the same Windows programs
    through the C: mount, because a window on the Windows side is the one a person at that machine
    can see. `prefer` is the default browser's short name, and it goes first."""
    rows = chromiums_in_order(prefer)
    if platform == "win32":
        pf = env.get("ProgramFiles") or r"C:\Program Files"
        pf86 = env.get("ProgramFiles(x86)") or r"C:\Program Files (x86)"
        la = env.get("LOCALAPPDATA") or ""
        out = []
        for _, _, win, _ in rows:
            for base in (pf, pf86, la):
                if base:
                    out.append(base.rstrip("\\") + "\\" + win)
        return out
    if kind:
        c = (cdrive or "/mnt/c").rstrip("/")
        out = []
        for _, _, win, _ in rows:
            rel = win.replace("\\", "/")
            out += [c + "/Program Files/" + rel, c + "/Program Files (x86)/" + rel]
        return out
    if platform == "darwin":
        return [mac for _, _, _, mac in rows]
    return []


def chromium_names(prefer: str = ""):
    """The PATH names, default browser first."""
    return [n for _, names, _, _ in chromiums_in_order(prefer) for n in names]


def browser_from_progid(progid: str) -> str:
    """Windows names the default browser by a ProgId: ChromeHTML, MSEdgeHTM, BraveHTML, ChromiumHTM,
    FirefoxURL-…. Short name, or '' for one this does not know."""
    p = (progid or "").lower()
    for key, name in (("chrome", "chrome"), ("msedge", "edge"), ("brave", "brave"),
                      ("chromium", "chromium"), ("firefox", "firefox"), ("opera", "opera")):
        if key in p:
            return name
    return ""


def browser_from_bundle(bundle_id: str) -> str:
    """macOS names it by bundle id: com.google.chrome, com.microsoft.edgemac, com.apple.safari…"""
    b = (bundle_id or "").lower()
    for key, name in (("com.google.chrome", "chrome"), ("com.microsoft.edgemac", "edge"),
                      ("com.brave.browser", "brave"), ("org.chromium.chromium", "chromium"),
                      ("com.apple.safari", "safari"), ("org.mozilla.firefox", "firefox")):
        if b.startswith(key):
            return name
    return ""


def browser_from_desktop(desktop: str) -> str:
    """Linux names it by a .desktop file: google-chrome.desktop, microsoft-edge.desktop, firefox.desktop…"""
    d = (desktop or "").lower()
    for key, name in (("google-chrome", "chrome"), ("chromium", "chromium"), ("microsoft-edge", "edge"),
                      ("brave", "brave"), ("firefox", "firefox")):
        if key in d:
            return name
    return ""


def progid_from_reg_output(text: str) -> str:
    """The ProgId out of `reg.exe query … /v ProgId` — the one line whose first word is ProgId,
    last word. The headings around it are localised; that line's shape is not."""
    for line in (text or "").splitlines():
        f = line.split()
        if len(f) >= 3 and f[0] == "ProgId":
            return f[-1]
    return ""


def default_browser_bundle() -> str:
    """macOS: the bundle id of the browser that handles http, Safari when nothing says otherwise."""
    try:
        import plistlib
        f = Path.home() / "Library/Preferences/com.apple.LaunchServices/com.apple.launchservices.secure.plist"
        with open(f, "rb") as fh:
            for h in plistlib.load(fh).get("LSHandlers", []):
                if h.get("LSHandlerURLScheme") == "http":
                    b = h.get("LSHandlerRoleAll") or h.get("LSHandlerRoleViewer") or ""
                    if b:
                        return b
    except Exception:
        pass
    return "com.apple.Safari"


def default_browser(platform=None, kind=None) -> str:
    """The person's default browser, by short name (chrome, edge, brave, chromium, safari, firefox…),
    or '' when it cannot be read cheaply. Never raises.

    Windows keeps it in the registry (HKCU …\\UrlAssociations\\http\\UserChoice ProgId); from WSL the
    same key is read with reg.exe through interop. macOS keeps it in LaunchServices' handler list;
    Linux answers `xdg-settings get default-web-browser`."""
    platform = sys.platform if platform is None else platform
    try:
        if platform == "win32":
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice") as k:
                return browser_from_progid(winreg.QueryValueEx(k, "ProgId")[0])
        if platform.startswith("linux"):
            kind = wsl_kind() if kind is None else kind
            if kind:
                s32 = windows_system32()
                exe = shutil.which("reg.exe") or (s32 and str(Path(s32) / "reg.exe"))
                if exe and Path(exe).is_file():
                    r = subprocess.run([exe, "query", r"HKCU\Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice",
                                        "/v", "ProgId"], capture_output=True, timeout=6, stdin=subprocess.DEVNULL)
                    return browser_from_progid(progid_from_reg_output(r.stdout.decode("utf-8", "replace")))
                return ""
            if shutil.which("xdg-settings"):
                r = subprocess.run(["xdg-settings", "get", "default-web-browser"], capture_output=True,
                                   timeout=4, stdin=subprocess.DEVNULL)
                return browser_from_desktop(r.stdout.decode("utf-8", "replace").strip())
        if platform == "darwin":
            import plistlib
            f = Path.home() / "Library/Preferences/com.apple.LaunchServices/com.apple.launchservices.secure.plist"
            with open(f, "rb") as fh:
                for h in plistlib.load(fh).get("LSHandlers", []):
                    if h.get("LSHandlerURLScheme") == "http":
                        return browser_from_bundle(h.get("LSHandlerRoleAll") or h.get("LSHandlerRoleViewer") or "")
            return "safari"                     # no http handler set means the one that ships
    except Exception:
        pass
    return ""


WINDOWS_APPDATA = [None]     # asked of cmd.exe once per start


def windows_appdata() -> str:
    """Windows's %APPDATA% as a Windows path, asked of cmd.exe through interop — '' off WSL or when
    interop is not there. Where Edge and Chrome leave the Start Menu shortcut of an installed app."""
    if WINDOWS_APPDATA[0] is None:
        WINDOWS_APPDATA[0] = ""
        if wsl_kind():
            s32 = windows_system32()
            exe = shutil.which("cmd.exe") or (s32 and str(Path(s32) / "cmd.exe"))
            if exe and Path(exe).is_file():
                try:
                    r = subprocess.run([exe, "/c", "echo %APPDATA%"], capture_output=True, timeout=6,
                                       stdin=subprocess.DEVNULL)
                    got = r.stdout.decode("utf-8", "replace").strip()
                    if re.match(r"^[A-Za-z]:\\", got):
                        WINDOWS_APPDATA[0] = got
                except (OSError, subprocess.SubprocessError):
                    pass
    return WINDOWS_APPDATA[0]


def windows_path_on_wsl(winpath: str, cdrive: str = "/mnt/c") -> str:
    """`C:\\Users\\me\\x` as WSL sees it: the drive's mount, then the rest with slashes."""
    m = re.match(r"^([A-Za-z]):\\(.*)$", winpath or "")
    if not m:
        return ""
    root = cdrive.rstrip("/") if m.group(1).upper() == "C" else "/mnt/" + m.group(1).lower()
    return root + "/" + m.group(2).replace("\\", "/")


def installed_pwa(env=None, exists=None, platform=None, kind=None, appdata=None, cdrive=None) -> str:
    """Where a browser put palmar when it was installed as an app — a Start Menu shortcut on Windows
    (Edge puts it in Programs, Chrome under Chrome Apps), an .app under ~/Applications on a Mac — or
    '' when it has not been installed. Launching that is launching the app the person installed, with
    its own icon and window, rather than a generic app-mode window.

    From WSL it is the **Windows** shortcut that counts (the window a person at that machine sees):
    %APPDATA% is asked of cmd.exe, looked for through the C: mount, and returned as the Windows path,
    which is what cmd.exe's `start` needs to open it."""
    env = os.environ if env is None else env
    exists = os.path.exists if exists is None else exists
    platform = sys.platform if platform is None else platform
    rels = (r"Microsoft\Windows\Start Menu\Programs\palmar.lnk",
            r"Microsoft\Windows\Start Menu\Programs\Chrome Apps\palmar.lnk")
    if platform == "win32":
        base = (env.get("APPDATA") or "").rstrip("\\")
        if base:
            for rel in rels:
                if exists(base + "\\" + rel):
                    return base + "\\" + rel
            # Browsers move the shortcut about (Chrome: "Chrome Apps", Edge: Programs itself, both
            # sometimes a folder of their own) — walk the Start Menu two levels down for it, and look
            # on the Desktop, where the install dialog also offers to put one.
            hit = shortcut_under(base + r"\Microsoft\Windows\Start Menu\Programs")
            if hit:
                return hit
            desk = (env.get("USERPROFILE") or "").rstrip("\\")
            if desk and exists(desk + r"\Desktop\palmar.lnk"):
                return desk + r"\Desktop\palmar.lnk"
    elif platform.startswith("linux"):
        kind = wsl_kind(env=env) if kind is None else kind
        if kind:
            base = (windows_appdata() if appdata is None else appdata).rstrip("\\")
            if cdrive is None:
                s32 = windows_system32()
                cdrive = str(Path(s32).parent.parent) if s32 else "/mnt/c"
            for rel in rels:
                if base and exists(windows_path_on_wsl(base + "\\" + rel, cdrive)):
                    return base + "\\" + rel
    elif platform == "darwin":
        home = (env.get("HOME") or "").rstrip("/")
        if home:
            for rel in ("Applications/Chrome Apps.localized/palmar.app",
                        "Applications/Edge Apps.localized/palmar.app",
                        "Applications/Chromium Apps.localized/palmar.app"):
                if exists(home + "/" + rel):
                    return home + "/" + rel
    return ""


def shortcut_under(root: str, depth: int = 2) -> str:
    """`palmar.lnk` anywhere up to `depth` levels under `root`, case-insensitively, or ''."""
    root = os.path.normpath(root)
    try:
        for cur, dirs, files in os.walk(root):
            if cur[len(root):].count(os.sep) >= depth:
                dirs[:] = []
            for f in files:
                if f.lower() == "palmar.lnk":
                    return os.path.join(cur, f)
    except OSError:
        pass
    return ""


def pwa_argv(path: str, platform=None, kind=None, cmd_exe=None):
    """The command that launches an installed app by its shortcut or bundle. From WSL the shortcut is
    a Windows path and cmd.exe's `start` opens it — cmd.exe found by name or under System32."""
    platform = sys.platform if platform is None else platform
    if platform == "win32":
        return ["cmd", "/c", "start", "", path]
    if platform.startswith("linux"):
        kind = wsl_kind() if kind is None else kind
        if kind:
            if cmd_exe is None:
                s32 = windows_system32()
                cmd_exe = shutil.which("cmd.exe") or (s32 and str(Path(s32) / "cmd.exe")) or "cmd.exe"
            return [cmd_exe, "/c", "start", "", path]
        return ["xdg-open", path]
    return ["open", path]


def app_mode_argv(url: str, *, platform=None, kind=None, env=None, which=None, exists=None, cdrive=None,
                  prefer=None):
    """The command that opens `url` as a window in a Chromium-family browser (`--app=`), or None.

    The person's default browser goes first when it is one of them — Edge opened on a machine whose
    browser is Chrome (user, 2026-09-15). `$PALMAR_CHROMIUM` names the browser to use (a path), or
    says `0` for none. Pure, like browser_argv: every input is injectable (tests/test_pure.py)."""
    platform = sys.platform if platform is None else platform
    env = os.environ if env is None else env
    which = shutil.which if which is None else which
    exists = (lambda p: Path(p).is_file()) if exists is None else exists
    chosen = env.get("PALMAR_CHROMIUM")
    if chosen is not None:
        chosen = chosen.strip()
        if chosen in ("", "0", "no", "off"):
            return None
        if exists(chosen):
            return [chosen, "--app=" + url]
        log(f"PALMAR_CHROMIUM points at nothing ({chosen}) — looking elsewhere")
    if kind is None:
        kind = wsl_kind(env=env) if platform.startswith("linux") else ""
    if kind and cdrive is None:
        s32 = windows_system32()
        cdrive = str(Path(s32).parent.parent) if s32 else ""
    if prefer is None:
        prefer = default_browser(platform=platform, kind=kind)
    for p in chromium_candidates(platform, env, kind, cdrive or "", prefer):
        if exists(p):
            return [p, "--app=" + url]
    if platform != "win32":
        for n in chromium_names(prefer):
            if which(n):
                return [n, "--app=" + url]
    return None


def notifier_argv(title: str, body: str, platform=None, env=None, which=None):
    """The command that shows an OS notification, or None. `$PALMAR_NOTIFIER` names a program that
    takes the title and the body as its two arguments, or says `0` for none.

    WKWebView has no Notification API, so palmar's own window on a Mac could not say "a terminal
    wants you" — the one promise the README makes about being away (found 2026-09-15). The page asks
    the daemon instead, and the daemon asks the OS: osascript on a Mac, notify-send on Linux. Windows
    shows the page in Edge or Chrome, which have the API, so nothing is needed there yet."""
    platform = sys.platform if platform is None else platform
    env = os.environ if env is None else env
    which = shutil.which if which is None else which
    chosen = env.get("PALMAR_NOTIFIER")
    if chosen is not None:
        chosen = chosen.strip()
        return None if chosen in ("", "0", "no", "off") else [chosen, title, body]
    if platform == "darwin" and which("osascript"):
        q = lambda t: t.replace("\\", "\\\\").replace('"', '\\"')   # noqa: E731 - AppleScript string quoting
        return ["osascript", "-e", f'display notification "{q(body)}" with title "{q(title)}"']
    if platform.startswith("linux") and which("notify-send"):
        return ["notify-send", "--app-name=palmar", "--", title, body]   # a title starting with - is not an option
    return None


def os_notify(title: str, body: str) -> bool:
    """Show an OS notification. False when there is no way to here."""
    argv = notifier_argv(title, body)
    if not argv:
        return False
    try:
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        log(f"could not notify with {Path(argv[0]).name} ({e})")
        return False
    return True


def window_order(platform=None):
    """How the page is shown, in order, per platform (decisions ④, 2026-09-15, user):

    - macOS: palmar's own window → the installed app (PWA) → a Chromium in app mode → a tab.
    - everywhere else: the installed app → app mode → palmar's own window → a tab.

    The Mac's window is WKWebView, which is the OS's own and light; on Linux the window is WebKitGTK,
    the least-tried engine here (its bugs — ibus preedit, compositing lag — each cost a commit), so a
    Chromium the person already runs comes first and the window is for a machine without one. Under
    WSL the Windows-side window wins over a WSLg one for the same reason, with fonts, IME and HiDPI
    on the Windows side. Windows has no window build yet, so its "app" tier is empty today."""
    platform = sys.platform if platform is None else platform
    return ("app", "pwa", "mode", "tab") if platform == "darwin" else ("pwa", "mode", "app", "tab")


def _start_app(url: str):
    """Tier "app": palmar's own window. `(note, lead)` — the note when it opened, else '' and a lead
    for the next tier's note saying why not."""
    app = find_app()
    if not app:
        return "", ""
    try:
        proc = subprocess.Popen([app], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, start_new_session=True)
    except OSError as e:
        log(f"could not start the window {app} ({e}) — trying the next way")
        return "", f"the window did not start ({e}) — "
    try:
        code = proc.wait(timeout=APP_GRACE_S)
    except subprocess.TimeoutExpired:
        code = None
    if code in (None, 0):
        log(f"opening the window ({app})")
        # Keep reading what it says: a pipe nobody drains fills, and a window that writes to a full
        # pipe blocks on its next line (review, 2026-09-15).
        threading.Thread(target=lambda: proc.stderr.read(), daemon=True).start()
        return "opening the window (palmar-app; --web opens a browser tab instead)", ""
    said = (proc.stderr.read() or b"").decode("utf-8", "replace").strip().splitlines()
    why = said[-1] if said else f"exit {code}"
    log(f"the window did not start ({why}) — trying the next way")
    return "", f"the window did not start ({why}) — "


def _start_pwa(url: str):
    """Tier "pwa": the app the person installed from Edge or Chrome — its own icon, window and Start
    Menu entry, and no binary of ours."""
    pwa = installed_pwa()
    if not pwa:
        return "", ""
    try:
        subprocess.Popen(pwa_argv(pwa), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        log(f"could not open the installed app {pwa} ({e}) — trying the next way")
        return "", "the installed app did not open — "
    log(f"opening the installed app ({pwa})")
    return "opening palmar as the installed app (--web opens a tab instead)", ""


def _start_mode(url: str):
    """Tier "mode": a Chromium-family browser in app mode — the page in its own window, no tabs, no
    address bar. Windows always has an Edge, so a window there needs no exe at all (user,
    2026-09-15); from WSL it is the Windows one, reached through interop."""
    try:
        target = launch_target(url)           # never the keyed address on a command line
    except OSError as e:
        log(f"could not write run/open.html ({e}) — trying the next way")
        return "", "could not write the opening file — "
    argv = app_mode_argv(target)
    if not argv:
        return "", ""
    try:
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        log(f"could not open a window with {Path(argv[0]).name} ({e}) — trying the next way")
        return "", f"could not open a window with {Path(argv[0]).name} — "
    log(f"opening a window with {Path(argv[0]).name} (app mode)")
    return f"opening a window with {Path(argv[0]).name} (app mode; --web opens a tab instead)", ""


TIERS = {"app": _start_app, "pwa": _start_pwa, "mode": _start_mode}


def show_page(url: str, web: bool = False) -> str:
    """Show the address the way this platform's order says (window_order), a tab last. One line
    back, for the printout next to the address.

    The window first (2026-09-15, user): the web button inside it is what opens a browser, and a
    `palmar` that opened a tab while the window sat right there had the two backwards. `--web` asks
    for a tab outright; PALMAR_APP=0 and PALMAR_CHROMIUM=0 empty their tiers. The window finds the
    daemon by run/url on its own, so it is started with no arguments and never handed the address."""
    lead = ""
    if not web:
        for tier in window_order():
            if tier == "tab":
                break
            note, why = TIERS[tier](url)
            if note:
                return lead + note
            lead += why
    ok, note = open_browser(url)
    return lead + note


#: A short-lived address: a nonce that `GET /once/<nonce>` turns into the keyed address for thirty
#: seconds. What goes on a command line where the keyed address cannot be replaced by a file.
#: **Not spent on first use** — it was, and Chrome fetched it twice: handed `--app=URL` for an origin
#: whose app is installed, it loads the URL and then moves it into the installed app's window, and
#: the second load met "this link is spent" (user, 2026-09-16, Windows). Where this address is used
#: — Windows, and WSL whose distro belongs to one Windows user — argv is not readable across
#: accounts, so the seconds are the guard, not the count.
ONCE: dict = {}
ONCE_TTL_S = 30
OPEN_FILE = RUN_DIR / "open.html"
#: True in the process that serves. A second `palmar` — the one that finds a daemon already up and
#: opens it — is not that process, and a nonce minted in its memory is unknown to the daemon that
#: gets the request: the browser met "this link has expired" on Windows (user, 2026-09-16).
SERVING = [False]


def mint_once() -> str:
    now = time.time()
    for n in [n for n, exp in ONCE.items() if exp < now]:
        ONCE.pop(n, None)
    n = secrets.token_urlsafe(24)
    ONCE[n] = now + ONCE_TTL_S
    return n


def take_once(n: str) -> bool:
    """Is this nonce still good? Any number of times inside its seconds; nothing after."""
    exp = ONCE.get(n)
    if exp is None:
        return False
    if exp < time.time():
        ONCE.pop(n, None)
        return False
    return True


def is_once_url(candidate: str, base: str) -> bool:
    """Is this what a daemon of ours would hand back from `/api/once`, and nothing else?

    The answer goes onto a command line and into a browser, so it is checked rather than trusted:
    http, the loopback, the same port we asked, and the `/once` path in either spelling. The check
    this replaces was `"/once/" in got`, which a URL like `http://elsewhere/x/once/y` satisfies."""
    try:
        u = urlparse(candidate)
        want = urlparse(base)
    except ValueError:
        return False
    if u.scheme != "http" or not (u.path == "/once" or u.path.startswith("/once/")):
        return False
    if u.hostname not in ("127.0.0.1", "localhost", "::1"):
        return False
    try:
        return u.port == want.port
    except ValueError:                  # a port that is not a number
        return False


def once_from_daemon(url: str) -> str:
    """Ask the running daemon at `url` for a short-lived address of its own minting (POST /api/once),
    the way _ask_to_stop asks it to stop: with the token from run/token. '' when it will not say."""
    try:
        token = TOKEN_FILE.read_text("utf-8").strip()
    except OSError:
        return ""
    base = url.split("/?")[0]
    req = urllib.request.Request(base + "/api/once?token=" + token, data=b"",
                                 headers={"Origin": base}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            got = json.loads(r.read().decode("utf-8", "replace") or "{}").get("url")
            return got if isinstance(got, str) and is_once_url(got, base) else ""
    except Exception:
        return ""


def is_palmar_window(title: str, cls: str) -> bool:
    """A top-level window that is palmar's page in a Chromium (app mode, an installed app, a tab):
    the class every Chromium window has, and the page's title — "palmar", or with the badge in
    front of it. The terminal that ran `palmar` may be titled palmar too; its class is not this."""
    return cls == "Chrome_WidgetWin_1" and "palmar" in (title or "").lower()


def raise_windows_window() -> bool:
    """Windows: bring palmar's window to the front. Runs in the process the person typed into — the
    one with foreground rights; a detached child has none, which is why it is not the daemon."""
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32          # type: ignore[attr-defined]
    except (ImportError, AttributeError, OSError):
        return False
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        title = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, title, n + 1)
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        if is_palmar_window(title.value, cls.value):
            found.append(hwnd)
        return True

    user32.EnumWindows(each, 0)
    if not found:
        return False
    user32.ShowWindow(found[0], 9)             # SW_RESTORE — un-minimise if it was
    user32.SetForegroundWindow(found[0])
    return True


def ask_focus(url: str) -> int:
    """`POST /api/focus` on the running daemon: it tells every open page to come forward and says how
    many there are. -1 when there is no daemon to ask."""
    try:
        token = TOKEN_FILE.read_text("utf-8").strip()
    except OSError:
        return -1
    base = url.split("/?")[0]
    req = urllib.request.Request(base + "/api/focus?token=" + token, data=b"",
                                 headers={"Origin": base}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            got = json.loads(r.read().decode("utf-8", "replace") or "{}")
            return int(got.get("clients", 0))
    except Exception:
        return -1


def focus_existing(url: str) -> str:
    """`palmar` while palmar is already on screen: bring that window forward instead of opening one
    more. One line for the printout, or '' when there is no open window to bring (then a window is
    opened as usual). `--new` skips this.

    Every `palmar` used to open another window on the same daemon — the habit of a second tab, and
    not what a desktop program does (user, 2026-09-16). The daemon is asked to tell its pages to come
    forward (the page calls window.focus(); palmar's own window is told over IPC to focus); browsers
    mostly ignore a page asking for the front, so the OS is asked too where it can be: Windows by the
    window's class and title from the process that has foreground rights, a Mac by activating the
    app that holds the window, Linux with wmctrl when it is there, WSL through the Windows shell."""
    n = ask_focus(url)
    if n <= 0:
        return ""
    raised = False
    try:
        if sys.platform == "win32":
            raised = raise_windows_window()
        elif sys.platform == "darwin":
            pwa = installed_pwa()
            argv = None if find_app() else app_mode_argv("about:blank")
            target = pwa or (str(Path(argv[0]).parents[2]) if argv and "/Contents/MacOS/" in argv[0] else "")
            if target:
                subprocess.Popen(["open", target], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
                raised = True
            else:
                raised = bool(find_app())           # palmar's own window focuses itself over IPC
        elif wsl_kind():
            s32 = windows_system32()
            ps = shutil.which("powershell.exe") or (s32 and str(Path(s32) / "WindowsPowerShell/v1.0/powershell.exe"))
            if ps and Path(ps).is_file():
                subprocess.Popen([ps, "-NoProfile", "-NonInteractive", "-Command",
                                  "(New-Object -ComObject WScript.Shell).AppActivate('palmar')"],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                raised = True
        elif shutil.which("wmctrl"):
            subprocess.Popen(["wmctrl", "-a", "palmar"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            raised = True
    except OSError as e:
        log(f"could not bring the window forward ({e})")
    return ("brought the open window to the front (--new opens another)" if raised
            else "already open — asked the %d open page%s to come forward (--new opens another)" % (n, "" if n == 1 else "s"))


def loopback_name(port: int, connect=None) -> str:
    """The host to put in an address handed to a browser: `localhost` when it reaches this daemon,
    `127.0.0.1` when it does not.

    **A dot in the host costs the window its size.** Chromium saves an `--app` window's bounds under
    a key made of the host and the path, and it writes that key by dotted path while reading it back
    flat — so anything saved under `127.0.0.1_/once` lands three levels deep and is never found again
    (measured on a Mac, 2026-09-16: Preferences held `{"127":{"0":{"0":{"1_/":…}}}}` and a 700x700
    window did not come back). `localhost` has no dot, and both allowlists already take it.

    It is **checked, not assumed**. The daemon binds 127.0.0.1 alone, so a machine whose `localhost`
    is ::1 and nothing else would otherwise be handed an address that goes nowhere."""
    connect = socket.create_connection if connect is None else connect
    try:
        s = connect(("localhost", port), 0.5)
    except Exception:
        return "127.0.0.1"
    try:
        s.close()
    except Exception:
        pass
    return "localhost"


def launch_target(url: str, platform=None, kind=None, host=None) -> str:
    """What a browser is handed in place of the keyed address — never the address itself.

    **The key was on the command line.** `open URL`, `chrome --app=URL`, `xdg-open URL` all put the
    keyed address in argv, and argv is public: /proc/<pid>/cmdline is world-readable, `ps` shows every
    account's arguments on a Mac, and a first-instance Chromium keeps its argv for as long as the
    window lives. Another account on the machine — the adversary decisions.md names — read the key
    with one `ps | grep k=` and had the token, and shells, a moment later (review, 2026-09-15).

    On a Mac or a Linux the browser gets a **file**: `~/.palmar/run/open.html`, 0600, a refresh to
    the keyed address. argv shows a path only this uid can read. Where a file cannot cross — from
    WSL to the Windows browser, or on Windows itself — it gets a **one-time address**,
    `/once/<nonce>`, that the daemon turns into the keyed one for thirty seconds."""
    platform = sys.platform if platform is None else platform
    kind = wsl_kind() if kind is None else kind      # from the environment, so a test can say "WSL" anywhere
    if platform == "win32" or kind:
        if SERVING[0]:
            # **The nonce rides in the query, not the path.** Chromium keys an --app window's saved
            # bounds on host and path, so a nonce in the path minted a new key every launch and the
            # window came up at Chromium's default size however wide it had been dragged (measured
            # on the user's Windows machine, 2026-09-16: a 1536x912 work area gave 1050x892, 1.18:1,
            # every time). A query is not part of that key.
            return f"http://{host or loopback_name(PORT[0])}:{PORT[0]}/once?n={mint_once()}"
        # Not the server: only the daemon can mint an address it will honour. Ask it; if it will not
        # answer (older daemon), the keyed address itself goes — on Windows argv is not readable across
        # accounts and a WSL distro belongs to one person, so that is the boundary it had anyway.
        got = once_from_daemon(url)
        if not got:
            log("the running daemon gave no short-lived address — handing the browser the address itself")
        return got or url
    page = ('<!doctype html><meta charset="utf-8"><title>palmar</title>'
            f'<meta http-equiv="refresh" content="0;url={url}">'
            f'<script>location.replace({json.dumps(url)})</script>'
            '<p style="font:15px system-ui;margin:12vh auto;max-width:34rem">Opening palmar…</p>\n')
    write_private(OPEN_FILE, page.encode(), 0o600)
    return OPEN_FILE.resolve().as_uri()


def open_browser(url: str):
    """Hand the address to a browser and do not wait. Returns `(ok, line)` — whether one was
    started, and one line saying how that went, for the printout next to the address.

    `url` is the keyed address; what reaches the command line is launch_target's stand-in for it.

    **Never raises.** Failing to open one is not a reason for the daemon not to run: the address is
    on stdout and in run/url either way. Everything it says goes to stderr, because stdout's last
    line is the address and that is a contract (docs/protocol.md)."""
    try:
        target = launch_target(url)
    except OSError as e:
        log(f"could not write run/open.html ({e}) — not opening a browser; open the address above yourself")
        return False, "could not write the opening file — open the address yourself"
    argv = browser_argv(target)
    if not argv:
        where = " — on Windows, for a palmar in WSL" if wsl_kind() else ""
        log("found no way to open a browser — open the address above yourself")
        return False, "no browser found here — open the address yourself" + where
    try:
        # start_new_session so the browser does not die with the daemon and never reaches for the
        # terminal; the pipes are closed so it cannot write over the address that was just printed.
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        log(f"could not open a browser with {Path(argv[0]).name} ({e}) — open the address above yourself")
        return False, f"could not start {Path(argv[0]).name} ({e}) — open the address yourself"
    # **Name the program, never the address** — the address carries the key.
    log(f"opening a browser with {Path(argv[0]).name} (--no-browser turns this off)")
    return True, f"opening a browser with {Path(argv[0]).name} (--no-browser turns this off)"


#: How far past the asked-for port to look before giving up. Twenty is more neighbours than any
#: machine has palmars, and small enough that "it is on 8803" is still a number a person can hold.
PORT_TRIES = 20


def windows_system32() -> str:
    """Where Windows's own programs are, seen from inside WSL — `''` when that cannot be found.

    Interop lets WSL run cmd.exe and friends, but only finds them by name when the Windows PATH is
    appended to the Linux one, and a locked-down or hand-tuned distro turns that off
    (`appendWindowsPath=false`) — which is why a `palmar` in Ubuntu printed an address and opened
    nothing (user, 2026-09-15). The C: drive's mount is read from /proc/mounts, with /mnt/c as the
    usual answer when it says nothing."""
    roots = []
    try:
        for line in Path("/proc/mounts").read_text("utf-8", "replace").splitlines():
            f = line.split()
            # /proc/mounts escapes the backslash of `C:\` as \134.
            if len(f) >= 3 and f[0].replace("\\134", "\\").rstrip("\\").upper() == "C:":
                roots.append(f[1])
    except OSError:
        pass
    for r in roots + ["/mnt/c"]:
        p = Path(r) / "Windows" / "System32"
        if (p / "cmd.exe").is_file():
            return str(p)
    return ""


def netstat_names_port(text: str, port: int) -> bool:
    """Does this `netstat -an -p tcp` output show a local socket on `port`? Pure — the text is
    handed in, so it is measured without a Windows (tests/test_pure.py).

    The state word is not looked at: only the local-address column, which is the same in every
    language Windows speaks. An established client socket on this port would count too, but clients
    take ephemeral ports from 49152 up and this range is far below that."""
    tail = ":%d" % port
    for line in text.splitlines():
        f = line.split()
        if len(f) >= 2 and f[0].upper() == "TCP" and f[1].endswith(tail):
            return True
    return False


def windows_holds_port(port: int) -> bool:
    """Under WSL: is something on the **Windows** side already on 127.0.0.1:`port`?

    WSL2's loopback is its own, so a bind here succeeds while a Windows program holds the same port —
    and the Windows browser, sent to 127.0.0.1:8801, reaches the Windows one. That is how a palmar
    started in Ubuntu opened the page of the palmar on Windows and was told its key was wrong
    (user, 2026-09-15). Windows's own netstat, run through interop, sees Windows's sockets."""
    if not wsl_kind():
        return False
    text = WINDOWS_NETSTAT[0]
    if text is None:
        text = ""
        exe = shutil.which("netstat.exe") or shutil.which("NETSTAT.EXE")
        if not exe:
            s32 = windows_system32()
            exe = str(Path(s32) / "NETSTAT.EXE") if s32 else ""
        if exe and Path(exe).is_file():
            try:
                text = subprocess.run([exe, "-an", "-p", "tcp"], capture_output=True, timeout=6,
                                      stdin=subprocess.DEVNULL).stdout.decode("utf-8", "replace")
            except (OSError, subprocess.SubprocessError):
                text = ""
        WINDOWS_NETSTAT[0] = text
    return netstat_names_port(text, port)


WINDOWS_NETSTAT = [None]     # one netstat per start, not one per candidate port


async def bind_somewhere(handle, port: int):
    """127.0.0.1:`port`, or the next free port after it — and a sentence for the printout when it moved.

    A taken port used to be the end: `palmard: could not bind`. But the person asked for palmar, not
    for 8801, and two palmars on one machine — one on Windows and one in WSL, or one per user — is an
    ordinary thing. The address they get is in the printout and in run/url either way, and `palmar`
    typed again shows it; only a bookmark on the old number goes stale, and the note says so
    (user, 2026-09-15). Never opened beyond 127.0.0.1 — there is no option for the host (#29)."""
    why = ""
    for p in range(port, port + PORT_TRIES):
        if windows_holds_port(p):
            why = "%d is held on the Windows side" % p
            continue
        try:
            server = await asyncio.start_server(handle, "127.0.0.1", p)
        except OSError as e:
            if e.errno not in (errno.EADDRINUSE, errno.EACCES):
                raise SystemExit(f"palmard: could not bind 127.0.0.1:{p} — {e.strerror or e}")
            why = "%d is in use" % p
            continue
        PORT[0] = p
        if p == port:
            return server, ""
        note_lock_line()
        log(f"port {port} was taken ({why}) — bound {p} instead")
        return server, (f"port {port} was taken ({why}) — this one is on {p}; "
                        f"a bookmark on {port} will not reach it")
    raise SystemExit(f"palmard: no free port from {port} to {port + PORT_TRIES - 1} on 127.0.0.1 ({why})")


def note_lock_line():
    """Rewrite run/lock's one line once the port is known — it is written before the bind, and
    `--doctor` reads the port out of it."""
    fd = LOCK_FH[0]
    if fd is None:
        return
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, f"pid {os.getpid()} http://127.0.0.1:{PORT[0]}\n".encode())
    except OSError:
        pass


def url_file_for_people() -> str:
    """`~/.palmar/run/url`, or the whole path when HOME is not above it."""
    try:
        return "~/" + URL_FILE.relative_to(Path.home()).as_posix()
    except ValueError:
        return str(URL_FILE)


def start_notes(browser_note: str = "", port_note: str = "", running: bool = False) -> list:
    """The few lines after the address — what a person needs at the moment they have it.

    The printout was the address alone, and the first thing asked after it was how to stop the
    thing (user, 2026-09-15). Three lines, stderr, after the address: stdout's last line is the
    address and that is a contract (docs/protocol.md)."""
    out = []
    if running:
        out.append("already running — this is that daemon's address (one daemon per HOME)")
    if port_note:
        out.append(port_note)
    if browser_note:
        out.append(browser_note)
    out.append("it keeps running after this terminal closes — `palmar --stop` ends it, shells and all")
    out.append("`palmar` again shows this address; so does `cat %s`" % url_file_for_people())
    return out


async def main(port: int, open_page: bool = True, web: bool = False, new: bool = False) -> None:
    PORT[0] = port
    UTF8_CTYPE[0] = pick_utf8_locale()
    try:
        TOKEN[0] = setup_palmar_dir()
    except AlreadyRunning as e:
        # **This is a success.** One daemon per HOME, and the page and the app (app/) are two views
        # of it — so `palmar` while it is already up means "show me palmar", not "start a second
        # one". The lock is taken before anything is rotated or deleted, so the running daemon's
        # token and its panes' hooks are untouched by this path.
        # The address still goes to stdout and only to stdout: "the last line is the address" is a
        # contract the app reads on (docs/protocol.md), and this path has to keep it.
        log("a daemon is already running — opening that one (one daemon per HOME)")
        # **announce, not print.** Detached, stdout is a log file and the only thing the person who
        # typed the command can still see is the pipe. This path is the ordinary one for a second
        # `palmar`, so sending its address down the log was the first thing detaching broke.
        how = ""
        if open_page and not new:
            how = focus_existing(e.url)         # the window already on screen, forward
        if open_page and not how:
            how = show_page(e.url, web)
        announce(e.url, start_notes(how, "", running=True))
        return
    # **Canvases come back on their own; terminals are offered.** A canvas is data — restoring it
    # surprises nobody. A terminal is a process, and starting eight of them is a thing a person
    # should press once (protocol.md "되살리기"). If there is nothing to restore this is the same
    # single unnamed canvas as before — there is never a moment without one (⑪).
    RESTORE[0] = read_restore()
    named = (RESTORE[0] or {}).get("canvases") or []
    if named:
        for c in named:
            made = registry.new_canvas(c.get("name") if isinstance(c.get("name"), str) else None)
            if isinstance(c.get("id"), str):
                RESTORE_CV[0][c["id"]] = made.id
        log(f"restored {len(named)} canvas(es) from {RESTORE_FILE.name}")
    else:
        registry.new_canvas()
    SETTINGS[0] = read_settings()
    registry.layout = read_layout()
    # **A viewer's canvas id has to be remapped like a pane's.** Canvases come back with fresh ids, and a
    # board entry naming a dead one leaves that window on no canvas at all: hidden on every tab, unreachable
    # from the rail, never cleaned up (review, 2026-09-15). RESTORE_CV is the same old→new map the restored
    # panes go through; anything it cannot place lands on the first canvas.
    if registry.layout:
        dflt = registry.default_canvas().id
        for e in registry.layout.values():
            cv = e.get("canvas")
            if cv is not None and cv not in registry.canvases:
                e["canvas"] = RESTORE_CV[0].get(cv, dflt)
        log(f"the board came back from {LAYOUT_FILE.name} — {len(registry.layout)} window(s)")
    loop = asyncio.get_running_loop()
    reaper.install(loop)
    server, port_note = await bind_somewhere(handle, port)
    port = PORT[0]
    SERVING[0] = True
    stop = loop.create_future()
    # Handed to the one HTTP route that can ask for a shutdown, so it goes out the same door as Ctrl-C.
    STOP_NOW[0] = lambda: None if stop.done() else stop.set_result(None)
    # **add_signal_handler is POSIX-only** — the Proactor loop raises NotImplementedError for it,
    # measured on a runner. `signal.signal` works on both, but its handler runs on the main thread
    # rather than inside the loop, so it has to hand back across with call_soon_threadsafe.
    def _stop_now():
        if not stop.done():
            stop.set_result(None)

    stop_signals = [signal.SIGINT, signal.SIGTERM]
    if hasattr(signal, "SIGBREAK"):
        # Windows only. It is what a console CTRL_BREAK arrives as, and that is how `--stop` reaches
        # a detached daemon there without resorting to TerminateProcess.
        stop_signals.append(signal.SIGBREAK)
    for sig in stop_signals:
        try:
            loop.add_signal_handler(sig, _stop_now)
        except (NotImplementedError, AttributeError, ValueError):
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(_stop_now))
    log(f"palmard pid {os.getpid()}  shell={os.environ.get('SHELL') or '/bin/sh'}  web={WEB}"
        f"{'' if (WEB / 'index.html').is_file() else ' (no index.html — serving a placeholder)'}")
    # The last line — this is all the user reads to get started. **The key is attached** (#14): only this
    # address gives you the page. The key persists, so this address is the same on the next start — bookmark it.
    url = f"http://127.0.0.1:{port}/?k={KEY[0]}"
    # Also left in a file (0600) so nobody has to scroll the terminal for it. The doctor names this path.
    try:
        write_private(URL_FILE, url.encode() + b"\n", 0o600)
    except OSError as e:
        log(f"could not write run/url — {e}")
    # The browser is asked first so the printout can say how that went — open_browser never raises and
    # never waits, so the address is not held up by it. Everything but the address goes to stderr, so
    # stdout's last line stays the address (docs/protocol.md).
    how = show_page(url, web) if open_page else ""
    announce(url, start_notes(how, port_note))

    # Polled rather than hooked into every broadcast: what it watches is the *directory* a pane sits
    # in, which changes with a `cd` that may print nothing and fire no event. Ten seconds is the most
    # that can be lost to a kill -9; a clean stop saves on the way out.
    def tick():
        # save_restore asks every pane where it is, so a `cd` that printed nothing reaches the rail
        # on this same ten seconds — and on a clean stop, which also saves.
        save_restore()
        for x in registry.list():
            try:
                x.sample_fg()            # a silent command is still seen within ten seconds
            except Exception:
                pass
        RESTORE_TIMER[0] = loop.call_later(RESTORE_EVERY_S, tick)
    RESTORE_TIMER[0] = loop.call_later(RESTORE_EVERY_S, tick)
    arm_update_check(loop)
    await stop
    server.close()
    shutdown()
    # Only a short gap for what was closed to settle. It leaves even if not everything finished — asyncio.run cancels the rest.
    try:
        await asyncio.wait_for(server.wait_closed(), 2.0)
    except Exception:
        pass


def _restored_cwd(name, fallback):
    """The folder restore.json holds for a pane, by name. None when it is not in there.

    Matched on the name because the file keeps no ids — a restored pane is a **new** pane, so an id
    would mean nothing to the daemon that reads it back (protocol.md)."""
    try:
        body = json.loads(RESTORE_FILE.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    for row in body.get("sessions") or []:
        if row.get("name") == name or (not name and row.get("cwd") == fallback):
            return row.get("cwd")
    return None


#: Principals that mean "other accounts on this machine" in a Windows ACL. Any of them on ~/.palmar
#: and the key, the token and the shells' hooks are readable by somebody who is not this person.
ACL_STRANGERS = ("everyone", "builtin\\users", "authenticated users", "interactive", "network")


def acl_strangers(icacls_text: str) -> list:
    """The principals in `icacls <dir>` output that are not this person, SYSTEM or Administrators.

    Measured on a real Windows (user, 2026-09-16): the profile's default ACL gives ~/.palmar to
    `NT AUTHORITY\\SYSTEM`, `BUILTIN\\Administrators` and `OWNER RIGHTS` (the owner, whoever that is),
    all `(OI)(CI)(F)`, and nothing else — so the default is enough and no permission management
    is added. This only says when a machine's default is not that (Codex review, item 3)."""
    found = []
    for line in (icacls_text or "").splitlines():
        low = line.lower()
        for who in ACL_STRANGERS:
            if who in low and ":(" in low:
                found.append(line.strip().split(":(")[0].strip())
    return sorted(set(found))


def doctor(port: int) -> int:
    """`palmar --doctor` — puts **this code**, **the running daemon** and **each pane's status** on one screen.

    It is here so a report that the lights do not come on does not become guesswork round trips. What the first
    block screens out is the most common cause: the checked-out code and the running daemon are **different
    versions** (it was never restarted). It makes no new contract — it reads only what `protocol.md` already has."""
    import platform, subprocess, urllib.error, urllib.request

    def out(*a):
        print(*a)

    out("palmar --doctor")
    out("─" * 64)
    out("this code")
    out("  version   %s   protocol %d" % (__version__, PROTOCOL))
    out("  file      %s" % Path(__file__).resolve())
    repo = Path(__file__).resolve().parent.parent
    if (repo / ".git").exists():
        def git(*a):
            try:
                return subprocess.run(["git", "-C", str(repo)] + list(a),
                                      capture_output=True, text=True, timeout=5).stdout.strip()
            except Exception:
                return "?"
        out("  commit    %s  (%s)" % (git("rev-parse", "--short", "HEAD"), git("log", "-1", "--format=%s")[:48]))
        out("  remote    %s" % (git("remote", "get-url", "origin") or "(none)"))
        st = git("status", "--porcelain")
        if st:
            out("  ! %d uncommitted change(s) in the working tree" % len(st.splitlines()))
    # The only thing that would ever leave this machine, and which way it is pointing.
    _s = read_settings()
    if update_check_on(settings=_s):
        _at = _s.get("checked_at")
        out("  update    on — asks %s once a day%s" % (
            LATEST_URL,
            ("   (last %s)" % time.strftime("%Y-%m-%d %H:%M", time.localtime(_at))) if isinstance(_at, (int, float)) and _at else ""))
    else:
        out("  update    off — palmar opens no connection of its own")
    out("")
    out("this machine")
    out("  python    %s  (%s)" % (platform.python_version(), sys.executable))
    out("  platform  %s" % platform.platform())
    out("  $SHELL    %s%s" % (os.environ.get("SHELL") or "(none)",
                              "   <- with none set it falls back to /bin/sh" if not os.environ.get("SHELL") else ""))
    # How the page gets opened, and from where. **On WSL that is the whole question** — the daemon is
    # in Linux and the browser is usually on the Windows side. The program is named and the address
    # never is: the address carries the key. A keyless URL is passed in for the same reason.
    kind = wsl_kind()
    tab = browser_argv("http://127.0.0.1:%d/" % port)     # keyless on purpose — never print the key
    out("  WSL       %s" % (kind or "no"))
    # **The directory rail, which came up empty on Windows and could only be guessed at from here**
    # (user, 2026-09-14). Three things decide what it shows: the tops to browse from, the roots that
    # are the floor for opening a terminal, and whether the entry actually builds. Printing all
    # three turns "the rail is empty" into one line that says which.
    try:
        tp = tops()
        rs = roots()
        out("  tops      %s" % (", ".join(tp) or "(none)"))
        out("  roots     %s" % (", ".join(str(r) for r in rs) or "(none)"))
        first = tp[0] if tp else None
        if first:
            e = dir_entry(first, first)
            out("  rail      %s -> %s" % (first, json.dumps(e, ensure_ascii=False)))
    except Exception as e:
        out("  ! the rail would fail here — %s: %s" % (type(e).__name__, e))
    out("  browser   %s%s" % (Path(tab[0]).name if tab else "(found no way to open one — open the address yourself)",
                              "   <- $BROWSER" if (os.environ.get("BROWSER") or "").strip() else ""))
    if sys.platform == "win32":
        # Who else can read ~/.palmar. The default profile ACL is the person, SYSTEM and Administrators;
        # anything wider is worth a line, because the key and the token live here.
        try:
            r = subprocess.run(["icacls", str(PALMAR_DIR)], capture_output=True, text=True, timeout=10)
            bad = acl_strangers(r.stdout)
            out("  acl       %s" % ("SYSTEM, Administrators and you — as it should be" if not bad else
                                    "! readable by %s — the key and the token are in here. Remove them: "
                                    "icacls \"%s\" /remove:g %s" % (", ".join(bad), PALMAR_DIR, " ".join('"%s"' % b for b in bad))))
        except (OSError, subprocess.SubprocessError) as e:
            out("  acl       (icacls did not answer — %s)" % e)
    # The window comes before the browser when `palmar` opens the page — say which one it would be:
    # palmar's own, a Chromium-family browser in app mode, or none (a tab, then).
    app = find_app() or installed_pwa()
    mode = None if app else app_mode_argv("http://127.0.0.1:%d/" % port)
    dflt = default_browser()
    out("  window    %s" % (app if app else
                            (Path(mode[0]).name + " in app mode (--app=)") if mode else
                            "(none — `palmar` opens a browser tab; a Chromium-family browser or app/README.md gives it a window)"))
    out("  default   %s" % (dflt or "(could not read which browser is the default)"))
    # The pane's character encoding. If it is not UTF-8, Korean, Japanese and Chinese input breaks — on screen it looks like "it will not type".
    loc = " ".join("%s=%s" % (k, os.environ[k]) for k in ("LC_ALL", "LC_CTYPE", "LANG") if os.environ.get(k))
    utf8 = has_utf8(os.environ)
    if sys.platform == "win32":
        # **There is no LANG on Windows and there does not need to be.** A ConPTY speaks UTF-8 and
        # Python decodes filenames as UTF-8 there regardless, so "(none) <- not UTF-8" was a warning
        # about a problem that does not exist on that platform (user, 2026-09-14).
        out("  locale    %s   <- not used on Windows; the console is UTF-8" % (loc or "(none)"))
        out("  panes get the console's own UTF-8")
    else:
        out("  locale    %s%s" % (loc or "(none)", "" if utf8 else "   <- not UTF-8"))
        out("  panes get %s" % ("this, unchanged" if utf8 else "LC_CTYPE=" + (UTF8_CTYPE[0] or pick_utf8_locale())))
    out("  home      %s" % PALMAR_DIR)
    # **Is the thing that makes the lights work actually attached?** AGENTS principle 3: a pane with
    # no hooks must not be left quietly grey. On Windows every one of these was missing at once and
    # the only way to find out was to read the source (2026-09-17), so it says which, by name.
    if POSIX_PERMS:
        shim = BIN_DIR / "claude"
        out("  hooks     %s%s" % (shim, "" if shim.exists() else "   <- MISSING; `claude` gets no --settings"))
        out("            zsh %s · bash %s" % (
            "ok" if (ZDOT_DIR / ".zshrc").exists() else "MISSING",
            "ok" if BASHRC.exists() else "MISSING"))
    else:
        out("  hooks     %s%s" % (PWSH_FILE, "" if PWSH_FILE.exists() else "   <- MISSING"))
        out("            PowerShell panes only — one opened with cmd.exe gets no hooks (#30)")
    out("  PATH      panes get %s first" % BIN_DIR)
    out("  shell     %s" % " ".join(Pty_default_shell()))
    out("")

    # **Is a daemon running** — screened by the lock. The token file outlives a dead daemon, so it is no evidence.
    # A daemon holds an exclusive flock on run/lock while it lives, and writes its pid and real address inside.
    lock_path = RUN_DIR / "lock"
    running, note = None, ""
    if lock_path.exists():
        try:
            fd = os.open(str(lock_path), os.O_RDWR | BINARY)
            try:
                lock_fd(fd)
                running = False                 # we took it = nobody is holding it
                unlock_fd(fd)
            except OSError:
                running = True                  # somebody is holding it = a daemon is alive
                try:
                    note = os.read(fd, 256).decode("utf-8", "replace").strip()
                except OSError:
                    note = ""
            os.close(fd)
        except OSError:
            pass

    if running is False or not TOKEN_FILE.exists():
        out("running daemon   **none**")
        if TOKEN_FILE.exists():
            out("  (%s is still there, but it belongs to a previous daemon — it is not evidence one is alive)" % TOKEN_FILE.name)
        out("")
        out("-> start it with `python3 -m palmar`, then run this again.")
        return 1

    token = TOKEN_FILE.read_text().strip()
    # The key itself is **never printed.** This output exists to be pasted, and the key is the right to be
    # handed the page (#14). Say only whether it exists and what its mode is, and where to get the address back.
    if KEY_FILE.exists():
        # **Both are checked.** run/url is a second copy holding the whole key, so its mode matters just as much.
        for label, f in (("key ", KEY_FILE), ("url ", URL_FILE)):
            if not f.exists():
                out("  %s      (%s is missing)" % (label, f.name))
                continue
            mode = oct(f.stat().st_mode & 0o777)
            out("  %s      %s (%s)%s" % (label, f, mode,
                                         "" if mode == "0o600" else "   <- should be 0600"))
        if URL_FILE.exists():
            out("            lost the address? `cat %s`" % URL_FILE)
    else:
        out("  ! no key file (%s) — this daemon predates the page key." % KEY_FILE)
    if note:
        out("running daemon   %s" % note)
        m = re.search(r":(\d+)", note)
        if m and int(m.group(1)) != port:
            out("  ! that daemon is on **port %s**. This asked about %d." % (m.group(1), port))
            out("    -> try again with `python3 -m palmar --doctor --port %s`." % m.group(1))
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
        out("  ! the lock is held but 127.0.0.1:%d would not answer — %s" % (port, e))
        out("    It is still starting, it just died, or it bound somewhere else.")
        return 1

    ver = _daemon_hello_version(port, token)
    out("")
    if ver is None:
        out("  version   **it does not say** — a daemon older than the protocol version")
        out("  ! restart it on the code you have checked out (that daemon is running older code)")
    else:
        same = ver.get("daemon") == __version__ and ver.get("v") == PROTOCOL
        out("  version   %s   protocol %s   %s"
            % (ver.get("daemon"), ver.get("v"), "(same as this code)" if same else "**different from this code**"))
        if not same:
            out("  ! the running daemon is not this code — stop it and start it again. Your fixes are not in it.")
    out("  sessions  %d" % len(sessions))
    out("")
    out("pane by pane")
    if not sessions:
        out("  (none — open a terminal in the browser and run this again)")
        return 0

    WATCH_S, STEP = 10.0, 0.5
    out("  Watching for %d seconds — one snapshot shows the value now but not" % WATCH_S)
    out("  **whether it moves**. Give an agent something to do in a pane meanwhile.")
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
        # **Where it would come back.** `cwd` in the API is the folder the pane was *opened* in; what
        # a restart actually reopens is what the restore file holds, which is `cwd_of(pid)` — the
        # folder it is in **now**. Those two being identical for every pane is exactly what "it does
        # not remember where I cd-ed to" looks like, and there was no way to see it from here
        # (user, 2026-09-14). Reading the file needs no pid, so the doctor can say it.
        was = s.get("cwd")
        now = _restored_cwd(s.get("name"), was)
        if now is None:
            out("      folder    %s   <- nothing in restore.json for it yet (saved every %.0fs)"
                % (was, RESTORE_EVERY_S))
        elif os.path.normcase(os.path.normpath(now)) == os.path.normcase(os.path.normpath(was)):
            out("      folder    %s   <- opened here, and still here as far as palmar can tell" % was)
        else:
            out("      folder    %s\n                -> %s   <- it followed a cd" % (was, now))
        out("      now       status=%s" % s.get("status"))
        # **Which layer, in the order they actually win.** Saying "output activity" while OSC 133 is
        # driving would be a diagnostic that lies, which is worse than none.
        if s.get("agent"):
            src = "hooks — %s reports it directly (the most exact)" % s.get("agent")
        elif s.get("shell") and not s.get("running"):
            src = "the shell (OSC 133) — it says it is at a prompt, and that is the last word"
        elif s.get("shell"):
            src = ("the shell (OSC 133) says a command is running; what the agent inside it is "
                   "doing comes from %s" % ("the window title: %r" % title if title
                                            else "output activity — this pane sets no window title"))
        elif title:
            src = "the window title — this pane sets one: %r" % title
        else:
            src = "output activity — this pane sets no window title"
        out("      read from %s" % src)
        out("      %s" % ("the agent field is filled by hooks. With an agent that has none it is "
                          "correctly empty, and it says nothing about the status."
                          if not s.get("agent") else "hooks are attached."))
        out("      over %.0fs  %s   %s" % (WATCH_S, " ".join(seen) or "(could not read)",
                                        "<- it moves" if moved else "<- **never changed once**"))
        if not moved:
            out("        (i=idle w=working d=done — was anything really running in that pane while watching?)")
        out("")
    out("what to look at")
    out("  - if a line above **never changed**: first check something was running while it")
    out("    watched. If it still does not move, send this output as it is — everything the")
    out("    daemon can see is in it.")
    out("  - if 'running daemon' says **different from this code**, start there — restart it.")
    return 0


def _daemon_hello_version(port: int, token: str):
    """Attaches to /events and reads exactly one hello. Asks down an existing path so as to make no new contract."""
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


#: Where the address goes. Normally stdout. When the daemon has been detached it is a pipe back to
#: the process the person actually ran, which prints it and exits — so **"stdout's last line is the
#: address" stays true for whoever typed the command** (docs/protocol.md), even though the daemon
#: that produced it is no longer attached to their terminal.
ANNOUNCE = [None]


def announce(line: str, notes=()) -> None:
    """The address, and the few lines that go with it. Detached, all of it goes down the pipe to the
    process the person ran, which prints the address on stdout and the notes on stderr; in the
    foreground the same split happens right here."""
    notes = [n for n in notes if n]
    fd = ANNOUNCE[0]
    if fd is None:
        print(line, flush=True)
        for n in notes:
            print("  " + n, file=sys.stderr, flush=True)
        return
    try:
        os.write(fd, "\n".join([line] + notes).encode() + b"\n")
        os.close(fd)
    except OSError:
        pass
    ANNOUNCE[0] = None


def focus_from_here():
    """In the process the person typed into: if a daemon is up and has a page open, bring that page
    forward, print its address and the notes, and say so with 0. None when there is nothing to bring
    — no daemon, or a daemon with no page — and the ordinary start should go ahead."""
    try:
        url = URL_FILE.read_text("utf-8").strip()
    except OSError:
        return None
    if not url.startswith("http://") or not daemon_answers(url):
        return None
    how = focus_existing(url)
    if not how:
        return None
    print(url, flush=True)
    for n in start_notes(how, "", running=True):
        print("  " + n, file=sys.stderr, flush=True)
    return 0


def detached_no_fork(port: int, open_page: bool, web: bool = False, new: bool = False) -> int:
    """The same promise where there is no `fork` — Windows.

    **Start a second copy of ourselves, detached, and wait for it to answer.** `DETACHED_PROCESS`
    gives it no console, so closing the one you typed in does not reach it; `CREATE_NEW_PROCESS_GROUP`
    keeps a Ctrl-C in that console from being broadcast to it. Both are needed: either alone leaves
    one of the two ways a terminal takes its children with it.

    The address comes back by **waiting until the daemon answers**, not by reading run/url — a
    `kill -9` leaves that file behind, so the file alone is not evidence the port is alive. That is
    the same check `app/` already makes before reusing an address.

    The POSIX side hands the address back over a pipe instead, because there a fork can simply keep
    the write end. Passing a handle to a detached process on Windows is a different piece of work for
    the same answer, and this one costs a poll."""
    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    if open_page and not new:
        # From here, not the child: this process was started by the terminal in front, so Windows
        # lets it set the foreground window; the detached child could not.
        done = focus_from_here()
        if done is not None:
            return done
    try:
        ensure_private_dir(PALMAR_DIR)
        logf = open(str(PALMAR_DIR / "log"), "ab")
    except OSError as e:
        print("palmar: could not open the log file — %s" % e, file=sys.stderr)
        return 1
    # By script path, not `-m`: `-m` puts the caller's cwd first on sys.path (PYTHONSAFEPATH or not on
    # 3.9/3.10), and a cloned repository with a palmar/ or a json.py in it would then be what runs.
    root = WEB.parent.parent
    entry = root / "launch.py"
    argv = ([sys.executable, str(entry)] if entry.is_file() else [sys.executable, "-P", "-m", "palmar"]) \
        + ["--foreground", "--port", str(port)]
    if not open_page:
        argv.append("--no-browser")
    if web:
        argv.append("--web")
    if new:
        argv.append("--new")
    try:
        child = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=logf, stderr=logf, close_fds=True,
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP)
    except OSError as e:
        print("palmar: could not start the detached daemon — %s" % e, file=sys.stderr)
        return 1
    finally:
        logf.close()
    end = time.monotonic() + STOP_WAIT_S * 3
    born = time.time()
    while time.monotonic() < end:
        # Only an address the child wrote — one older than this start is a dead daemon's, and
        # whoever answers on it is not necessarily palmar (review, 2026-09-15).
        try:
            fresh = URL_FILE.stat().st_mtime >= born - 2
            url = URL_FILE.read_text("utf-8").strip() if fresh else ""
        except OSError:
            url = ""
        if url.startswith("http://") and daemon_answers(url):
            print(url, flush=True)
            # No pipe on this side, so the notes are composed here. The port is read back out of
            # the address: a moved one is the one thing worth saying about it.
            m = re.search(r":(\d+)/", url)
            got = int(m.group(1)) if m else port
            for n in start_notes("", "" if got == port else
                                 f"port {port} was taken — this one is on {got}; a bookmark on {port} will not reach it"):
                print("  " + n, file=sys.stderr, flush=True)
            return 0
        if child.poll() is not None:
            # It is gone. Whatever it had to say went to the log, so point at that rather than
            # inventing a reason — the same rule the POSIX side follows with its pipe.
            print("palmar: the daemon did not stay up (exit %s)\n        what happened: %s"
                  % (child.returncode, PALMAR_DIR / "log"), file=sys.stderr)
            return 1
        time.sleep(0.2)
    print("palmar: no address within %.0fs — look at %s"
          % (STOP_WAIT_S * 3, PALMAR_DIR / "log"), file=sys.stderr)
    return 1


def detached(port: int, open_page: bool, web: bool = False, new: bool = False) -> int:
    """Start the daemon in its own session and come straight back.

    **The terminal was killing it.** The daemon caught SIGINT and SIGTERM but not SIGHUP, and closing
    a terminal sends SIGHUP to the whole process group — measured 2026-09-14: the daemon was gone two
    seconds later. Wrapping it in the window did not fix that either; a window started *from* a
    terminal is in the same group. Being in a session of its own is the actual mechanism, and it makes
    the window optional rather than required (principle 2: sessions outlive the UI).

    One fork is enough. The parent prints the address and exits at once, so the daemon is reparented
    immediately and no zombie is left behind — a second fork only buys something for a parent that
    lingers, and this one does not.

    The address comes back over a pipe rather than by watching run/url, because a start that **fails**
    has to come back the same way. With stderr going to a log file, the pipe is the only thing the
    person who typed the command can still see."""
    if open_page and not new:
        done = focus_from_here()
        if done is not None:
            return done
    r, w = os.pipe()
    pid = os.fork()
    if pid:                                     # the process the person ran
        os.close(w)
        with os.fdopen(r, "rb") as back:
            said = back.read().decode("utf-8", "replace").strip()
        if not said:
            print("palmar: the daemon gave no address — try `palmar --doctor`", file=sys.stderr)
            return 1
        if not said.startswith("http://"):
            print(said, file=sys.stderr)        # it failed, and this is why
            return 1
        first, *notes = said.splitlines()
        print(first, flush=True)                # stdout: the address and nothing else
        for n in notes:
            print("  " + n, file=sys.stderr, flush=True)
        return 0

    # ── the daemon, from here on ───────────────────────────────────────────
    os.close(r)
    os.setsid()                                 # a fresh fork is never a group leader, so this holds
    # setsid already means a closing terminal's SIGHUP never reaches here — it goes to that terminal's
    # foreground group and this is not in it. Ignoring it as well covers the one sent by hand, which
    # is what a background service should do and costs a line.
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
    ANNOUNCE[0] = w
    try:
        ensure_private_dir(PALMAR_DIR)
        logf = os.open(str(PALMAR_DIR / "log"), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    except OSError:
        logf = os.open(os.devnull, os.O_WRONLY)
    null = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null, 0)
    os.dup2(logf, 1)                            # anything printed after the address goes to the log
    os.dup2(logf, 2)
    for fd in (null, logf):
        if fd > 2:
            os.close(fd)
    try:
        asyncio.run(main(port, open_page=open_page, web=web, new=new))
    except SystemExit as e:   # noqa: PERF203 - three separate reports, not one
        # The refusals — a lock somebody holds, a port in use — are SystemExit with a sentence. The
        # person who typed the command is on the other end of that pipe and has nothing else to read.
        announce(str(e.code) if e.code and not isinstance(e.code, int) else "palmar: it did not come up")
        os._exit(1)
    except BaseException as e:
        announce("palmar: %s: %s" % (type(e).__name__, e))
        os._exit(1)
    os._exit(0)


def cli() -> None:
    """The `palmar` command and `python3 -m palmar` both land here."""
    ap = argparse.ArgumentParser(prog="palmar", description="the palmar daemon — bound to 127.0.0.1 only")
    ap.add_argument("--port", type=int, default=8801, help="the port to ask for; the next free one is taken when it is busy")
    ap.add_argument("--version", action="version", version="palmar " + __version__)
    ap.add_argument("--doctor", action="store_true",
                    help="print what this code, the running daemon and each pane look like, then exit")
    # The address is printed either way. palmar's own window is a separate program (app/) that
    # passes --no-browser and loads the address itself.
    ap.add_argument("--no-browser", action="store_true",
                    help="print the address only, open no browser (what the app uses)")
    # The window comes first when there is one (2026-09-15) — this asks for a browser instead.
    ap.add_argument("--web", action="store_true",
                    help="open a browser even when palmar's own window is installed")
    # `palmar` while palmar is open brings that window forward (2026-09-16); this opens one more.
    ap.add_argument("--new", action="store_true",
                    help="open another window even when one is already open")
    # Closing a window does not stop the daemon — it holds live shells, and that is the point. So
    # there has to be a way to say stop, and it is this one (asked for 2026-09-11).
    ap.add_argument("--stop", action="store_true",
                    help="stop this HOME's daemon (the shells in it die with it)")
    # **Detached is the default**, because the daemon outliving the terminal is the point of it
    # (principle 2). --foreground is for developing on it and for the tests, which have to be able to
    # terminate what they started. Windows gets there a different way — see detached_no_fork.
    ap.add_argument("--foreground", action="store_true",
                    help="stay attached to this terminal (Ctrl-C stops it); detaching is the default")
    args = ap.parse_args()
    if args.stop:
        raise SystemExit(stop_daemon())
    if args.doctor:
        raise SystemExit(doctor(args.port))
    if args.foreground:
        asyncio.run(main(args.port, open_page=not args.no_browser, web=args.web, new=args.new))
    elif hasattr(os, "fork"):
        raise SystemExit(detached(args.port, open_page=not args.no_browser, web=args.web, new=args.new))
    else:
        raise SystemExit(detached_no_fork(args.port, open_page=not args.no_browser, web=args.web, new=args.new))

