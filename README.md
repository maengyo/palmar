# palmer

*Read this in [한국어](README.ko.md).*

> ⚠️ **Early.** The daemon (`server/palmerd.py`) and the browser UI (`web/`) now run — see **Try it**
> below. This document is still **the plan and the facts checked before code**, and several decisions
> are the human's to make (web stack, coordinate storage, colours, codex status, app release).

A spatial canvas for coding agents that are already running. Terminals sit where you put them,
at the size you gave them, and each one shows whether it is **working · waiting on you · done**.
Close the browser tab and the sessions are still there.

**The runtime is ours.** palmer does not sit on someone else's multiplexer — not herdr, not tmux,
not zellij. Instead it owns a narrow slice: spawn PTYs, move bytes, receive hooks from the CLIs,
remember where windows are. Drawing the terminal is xterm.js's job, in the browser.

**Three columns.** Pick a folder on the right and a shell opens on the canvas at that path; what
you run inside it is up to you. The list on the left shows which terminal is waiting on you — even
if that window got pushed aside, even if it is off screen.

```
┌─ left ────┬─ canvas ───────────────┬─ right ───┐
│ terminal  │ terminals sit where    │ directory │
│ list      │ you put them. Nothing  │ folders   │
│ waiting   │ overlaps: a new window │ only, ↻   │
│ on top    │ pushes its neighbour   │ read lazy │
└───────────┴───────────┬────────────┴───────────┘
                        │ WebSocket (count: open question)
┌───────────────────────┴────────────────────────┐
│  palmer daemon - owns PTYs, receives hooks,    │
│  remembers places. Alive with no browser.      │
└──────┬─────────────────────┬───────────────────┘
       │ PTY (raw bytes,     │ hooks - paired by pane id in the URL
       │ input, resize)      │ (a PATH shim adds them per pane)
┌──────┴─────────────────────┴───────────────────┐
│  pane: a shell. Run claude, codex, anything.   │
│  Each a real PTY. Hooks/SSE report status.     │
└────────────────────────────────────────────────┘
```

## Try it

```
python3 server/palmerd.py            # prints http://127.0.0.1:8801 on its last line — open that
```

One command, no arguments, no install: it needs `/usr/bin/python3` (3.9.6), which is present wherever
`git` is. It binds only `127.0.0.1`. Open the printed URL in a browser.

**What you see:** three columns — a terminal list on the left, a dot-grid canvas in the middle, a
directory rail on the right. Pick a folder on the right, press **Open terminal here**, and a shell
opens on the canvas at that path. Drag the title bar to move a window, the corner to resize (the
shell's rows and columns follow). Click the expand box to blow one up to the whole canvas; **Esc**,
or a click on the "back to canvas" pill, returns — and so does switching to a canvas the expanded
window does not live on. Close the tab and reopen the URL — the sessions are still there (the daemon
owns the PTYs, the browser just attaches).

**Canvases, above the canvas.** A tab strip sits over the canvas, OneNote-style. **＋** makes a new
canvas and asks for its name right away; double-click a tab (or press F2 on it) to rename it later;
drag tabs to reorder them. A tab carries **one small dot** when something in that canvas is waiting
on you — nothing else goes on a tab. The dot does not go out because you looked at it; the next hook
turns it off. Every open browser sees the same tabs in the same order, because the daemon owns them.
The strip stays honest while you work in it: a status change arriving mid-drag no longer disturbs the
tab you are holding, the dots keep updating while a rename box is open, and when you land on a canvas
whose tab is scrolled out of sight the strip brings that tab into view.

**The left list is never filtered by canvas.** Everything is there, grouped by status, waiting first,
whichever canvas it lives in. A session in another canvas carries a small canvas badge — click the
row and palmer switches to that canvas and focuses that window. Click a group header to collapse it;
the count stays, and the collapse survives a reload. While you are searching, collapsed groups open
so nothing hides from the filter.

**Names.** Both canvases and terminals can be named. On a window, the rename box next to the title —
or a double-click on the name — takes it; empty clears it and the path label comes back.

**Minimap.** Bottom-right of the canvas, for the canvas you are looking at, and only that one. The
canvas grows without limit as you add windows, so it answers "where am I in here"; click or drag in
it to move the viewport.

**What does not work yet:**
- **There is no button that deletes a canvas, and none that moves a terminal to another canvas.**
  The daemon does both (`DELETE /api/canvases/<id>`, `PATCH /api/sessions/<id>`) and every open
  browser follows along correctly — where those handles belong in the UI is still an open product
  question (⑪), so nothing was invented for them.
- **Many canvases push the ＋ off the end of the tab strip.** At 20 canvases the strip is more than
  twice as wide as its box and its scrollbar is hidden, so the ＋ is out of reach until you scroll
  sideways. The *current* tab is scrolled into view for you; the ＋ is not, and no overflow or
  truncation rule has been chosen yet.
- **There is no close button on a window either.** Type `exit` in the shell and the window goes away
  (measured: the session leaves the list and its per-pane settings file is swept). The browser never
  sends a DELETE — a window is a view of a session, and a session outlives the UI.
- **Nothing survives restarting the daemon** — not canvases, not names, not the shells. That is
  ⑦=b (no handoff) plus "nothing is written to disk"; it is what `docs/protocol.md` says.
- **Hooks attach in zsh, not yet in bash or fish.** Putting the shim first on `PATH` was not enough —
  the user's own `.zshrc` runs afterwards and re-prepends its directories. palmer now wraps zsh with
  `ZDOTDIR`, so its rc runs *last* and puts the shim back in front; the user's files are only read.
  Measured: a real `claude` in a pane reported `working` in three seconds with nothing installed.
  **bash and fish are not wrapped yet**, so their dots can stay grey.
- **Dragging one window onto another overlaps them** — the push-aside (#23) is not built yet.
- **The JetBrains Mono webfont is not downloaded** (no network at runtime): if it is not installed
  locally the UI falls back to your system monospace.
- No settings panel, no ⌘K-to-command, no codex/opencode hooks (⑧ undecided).

Stop it with Ctrl-C.

## Where this came from

The idea is [cate](https://github.com/0-AI-UG/cate) — an Electron IDE that puts terminals on a
zoomable canvas and attaches agent status to them. palmer is the narrow slice of that: terminals
and status lights, nothing else.

This is the fourth attempt. The record is not hidden.

- **polycanv on zellij** (2026-08-18 to 20): tiling, so free placement was never possible, and
  install took three lines. The first time an engine got picked before the requirements were asked.
- **polycanv** (2026-08, a Python TUI, also served to the browser via `textual-serve`): free
  placement, status lights, and hooks all worked. But the Python terminal emulator (pyte) burned
  8× the CPU of tmux under load (`seq 1 200000`: 1.50s vs 0.18s CPU, one terminal — polycanv itself
  noted this "is not a problem for watching AI CLIs"), being a TUI meant no per-pane scale and no
  overlap, and closing the browser tab killed every session (#21).
- **A client on top of herdr** (2026-09-01 to 04): a Rust multiplexer would own PTYs, persistence
  and status; palmer would be just the canvas. Spikes confirmed streaming and resize (headless, no
  TUI). Status only half worked — the question UI was caught, but the bash approval prompt failed
  detection both times, and because detection was a screen regex it misread a codex dialog as
  `idle` (`docs/herdr-api.md`). Dropped anyway, on the judgement that **depending on someone else's
  program is not worth it even when the features line up** (2026-09-07).
- **palmer** (now): instead of relaying a TUI, the browser draws. Emulation goes to xterm.js, the
  browser shrinks the glyphs (the path polycanv #20 already confirmed), and a daemon owns the PTYs
  so it outlives the UI (the shape #21 sketched). **The first two of those three were measured on
  2026-09-07** (spike D); outliving the UI is confirmed only as far as fd passing — reconnect
  restore is still open.

## Why build it — honestly

**This space is crowded.** As of 2026-09 there are already several products that put agent
terminals on a canvas (see the table in `docs/own-runtime.md`): cate (2.1k★, Electron IDE, 496MB
dmg), Collaborator (2.9k★, Electron), nodeterm (1.8k★, Electron + tmux, has a browser server
edition), OpenCove (1.6k★, Electron, experimental web UI), TermCanvas (394★), Horizon (704★, Rust,
23MB), CodeGrid (Tauri, 12.5MB), mulmoterminal (`npx`, browser grid). There are plenty of clients
on herdr too (`herdr-api.md`).

What palmer wants to do differently is four things: **browser only** (no Electron), **one-line
install including the runtime**, **no third-party multiplexer**, and **sessions that outlive the
UI**. The closest existing things are OpenCove's Worker plus its experimental web UI (has an
install script, Worker owns the PTYs) and nodeterm's server edition (tmux-backed, multi-line
install). palmer's place is making those four the default rather than the experiment.

**The search was five angles on 2026-09-07** (`docs/research/…`), so "nobody has done this" means
"the search did not find it", not that it does not exist. This is closer to a preference than to a
capability nobody else has.

## One frontend: the browser

The browser is the default, and the reason is **zoom**. `Ctrl+-` shrinks the cell, so every
terminal gets more rows and columns and more of them fit on the canvas. That is already what we
wanted, so **the canvas's own camera zoom is cut from the first version.** What replaces it is
putting that shortcut somewhere you can see it — an invisible feature is not a feature.

A terminal emulator can shrink its font too, but the whole app is one scale. Per-pane scale,
continuous zoom, and overlap are what we want, and a TUI has no path to them. polycanv hit the
"cannot shrink the glyphs" wall (#20) and solved it by leaving for the browser.

Open a Chromium-family browser with `--app=http://127.0.0.1:…` and you get a window with no
address bar, which reads like an app (Firefox and Safari have no equivalent).

When a real window is needed later, **the same web code goes into Tauri** (CodeGrid shows this at
12.5MB). Electron is the thing we are complaining about, so it is not the answer.

## What was checked before any code

**2026-09-07 — our runtime, measured (`docs/own-runtime.md`)**

- **Pane processes survive the daemon's death.** Passing the PTY master fd over a Unix socket to
  the next process and then killing the original daemon left the child running with no HUP, and
  resize worked through the passed fd. An orphaned child's exit was still caught at 0ms via kqueue
  (one separate run, SIGKILL).
- **Hooks arrive at the same moment the dialog does.** In Claude Code 2.1.259 the approval prompt
  appearing on screen and the `PermissionRequest` hook landed inside the same 50ms poll (screen
  detection resolution 50ms, one run). There is no distinguishing when a person sees it from when
  the hook fires. **AskUserQuestion comes through the same hook** — two kinds of dialog, one hook
  (other dialogs unverified). `Notification` is a delayed nudge that fires when an approval sits
  for more than six seconds (one observation, 6.0s). The spike layered this on with `--settings`;
  the product does the same through a PATH shim in every terminal it opens — nothing for the user to
  install (see "Settled" below).
- **There is a lot to take from polycanv** — hook layering, status merge rules, the Unix socket
  bridge, PTY handling, comparison benchmark tooling. The claude and codex hooks were measured in
  a TUI on 2026-08-19; opencode is spec only, and qwen only goes as far as `SessionStart`.

**2026-09-07 — research, agents checking docs, sources and local runs
(`docs/research/2026-09-07-own-runtime.md`)**

- **fd passing (handoff) works with the Python standard library** (`socket.send_fds`).
  **Node and Bun cannot do it.** Go and Rust probably can, but that was not in this research.
- **Bun has a built-in PTY and WebSocket server** (1.3.5+, **Windows needs 1.3.14+** — which
  matters because the app has to cover Windows). Node needs the node-pty native module, and stable
  1.1.0 ships prebuilds only for mac and win, with the mac one missing its executable bit
  (fixed in 1.2.0-beta). Python has PTY and fd passing in the standard library but no WebSocket,
  and macOS's `/usr/bin/python3` is a stub that prompts for the Xcode CLT (installing the CLT
  gets you 3.9.6).
- **Claude Code hooks have an `http` type**, so the daemon can receive them directly with no helper
  script. **Codex can write `Action Required` into the terminal title**, so status is readable
  without going through the hook trust gate (from source; not verified live).
- **codex and opencode TUIs default to alt screen; Claude Code is conditional** (fullscreen by
  default only for users who started after 2026-05-06, and env vars or saved settings flip it).
  Reconnect restore starts by measuring whether SIGWINCH is enough to force a redraw.
- **xterm.js 6** has only DOM and WebGL renderers, and WebGL contexts are commonly said to cap at
  16 per renderer process (the research could not confirm the current default constant either).
  Without flow control a single `yes` blocks input. A 6.0.0 bug on growing the size is still open.

**2026-09-01 to 04 — herdr (`docs/herdr-api.md`)** — now the comparison baseline: herdr server RSS
35MB, 6MB per pane controller (measured once). Plus a real case of screen-regex detection being
wrong.

## Settled on 2026-09-07 (confirmed against a sketch)

- **Directory rail on the right** — folders only, no files. Every home the user can reach is
  visible, and git repos show their branch. **A folder is read only when it is expanded**, and
  re-reading happens when the refresh button says so. `git status` is never run — that kind of
  per-repo polling cost cate 45% CPU.
- **Terminal list on the left** — whatever is waiting sits on top. Windows move when they get
  pushed; their place in the list does not.
- **palmer only opens shells** — it has no tool list. Typing `claude` is the user's job.
- **Hooks attach themselves.** Every shell palmer opens has `~/.palmer/bin` first on PATH; a
  two-line `claude` shim there execs the real one with `--settings` pointing at a per-pane file.
  No user config is touched and there is nothing to install. **Measured:** `--settings` merges with
  the other layers, it does not replace them (spike E). `command claude` or an absolute path
  bypasses the shim — then the light stays grey and palmer says why. This reversed the morning's
  "install once" decision; the premise behind it was wrong.
- **Nothing overlaps.** A new window lands where it is put and pushes its neighbour aside
  (termcanvas-style minimum translation). The overlap setting is gone — nothing overlaps, so there
  is nothing to switch.
- **One terminal can expand to fill the canvas.** Rows and columns genuinely grow; this is not a
  camera. The rails stay — while you are looking closely, you still need to see who is waiting.
- **Camera zoom is cut from the first version.** The browser's `Ctrl+-` is written where you can
  see it instead; it already shrinks the cell and shows more canvas. cate had to fix eight separate
  things because of camera zoom alone.
- **Folder creation only.** No creating, deleting, or renaming files — that is a file manager, and
  a terminal is right there.
- **Window corners are round.** But no macOS-style red/yellow/green buttons — those three colors
  belong to the status lights.
- **Desktop builds ship** — macOS, Windows, Linux AppImage, deb, wrapped with Tauri. Browser first.
- **WSL is solved by running the daemon inside WSL.**
- **"Light" means it does not stutter** — frame time and input latency come before size.
  **Measured on 2026-09-07:** 60fps held with eight panes all flooding (30MB/s) and with sixteen
  panes, with zero long tasks. Key-to-echo reaching the browser took 5-7ms — though that is not
  when it was painted (one more frame for that). Details in spike D of `docs/own-runtime.md`.

## Still unknown

- ~~The daemon's language~~ — **settled 2026-09-07: Python, floor at 3.9.** Both were built and
  measured and throughput did not separate them. Flow control did: when the browser falls behind,
  Python stops reading the PTY and the child blocks in `write()`; Bun has no way to — `Bun.Terminal`
  exposes neither a pause nor the fd, so there is nothing to signal (31MB against 87MB). Install
  settled it further: `/usr/bin/python3` is 3.9.6 and every spike runs on it, so there is nothing
  to download — the stub is byte-identical to `/usr/bin/git`, so a machine with git has it.
  **Kept reversible**: no Python-only structure, the protocol written down, terminal state outside
  the daemon. If Bun opens the fd, this gets looked at again.
- ~~Daemon restart strategy~~ — **settled 2026-09-07: the daemon does not restart.** Updates restart
  it; agents come back with `--resume` and shell panes die. Handoff was built and worked (spike G)
  but was not chosen — it would pin the language to Python and has to be designed in from the start.
- **Reconnect restore.** Is shaking SIGWINCH enough for an alt-screen agent? What about the
  Classic renderer?
- **Placement.** Five apps solved this five ways, and that is written up (`own-runtime.md`,
  "canvas"). Push-away is chosen; what happens when there is nowhere to push is not.
- **Whether camera zoom comes back later.** It is cut, but the research is done — three branches
  to pick from if it does.
- **When to ship the app.** Shipping turns "one-line install" into "download and run".
- **Whether it actually feels smooth still needs a person.** What was measured was an automated
  drag and a shell loop — a human hand, real agent output, and an integrated-GPU machine are all
  still ahead.
- How to guide hook installation, the codex trust gate, opencode SSE running for real.

## Documents

- [`README.ko.md`](README.ko.md) — this document in Korean
- `AGENTS.md` — the working agreement. The rules when handing work to an agent
- `docs/own-runtime.md` — measurements of our own runtime plus a research summary
  (fd passing, hook timing, PTY per language, restore, the competition)
- `docs/research/2026-09-07-own-runtime.md` — that day's raw research (five agents, with sources
  and confidence grades)
- `docs/herdr-api.md` — the herdr investigation. Now the comparison baseline and a list of what a
  runtime has to provide
- `docs/spikes/<date>/` — the measurement scripts written that day
- `docs/decisions.md` — what is decided, what was reversed, and **what is deliberately open**
- `docs/roadmap.md` — **the order, and what blocks what.** Four phases, dependencies, where we are
- `docs/autopilot.md` — the brief an unattended agent loop works from: cycle, what is decided, what it must not decide
- `docs/backlog.md` — task seeds from the herdr era. History now; no new tasks go here

**The living list is the [issues](https://github.com/maengyo/palmer/issues) and the
[board](https://github.com/users/maengyo/projects/3) (private).**

## Language

**English is the default for this repo's README and commit messages.** The Korean edition
([`README.ko.md`](README.ko.md)) is kept in step with it. The rest of `docs/` is Korean — it is
working material for one reader.

## License

MIT (the LICENSE file lands with the first code)
