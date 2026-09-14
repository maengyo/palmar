# palmar

*Read this in [한국어](README.ko.md).*

A light way to keep a lot of terminals in one place.

> **palmar is not an AI tool.** It runs no model, calls no API, and sends your work nowhere.
> It opens shells and shows you where they are. The agents are the ones you already run, in the
> terminals you already use — palmar only makes a screenful of them easy to live with.

Run several coding agents at once and you have several terminals that look alike, one of which
stopped a minute ago and is waiting on an answer you have not given. palmar puts them on a canvas.
Each terminal keeps the place and the size you gave it, and each carries a light: **working**,
**waiting on you**, **done**, **idle**. It works out which without being told anything about the
agent inside.

Close the browser tab and the sessions keep running.

![palmar running: eight terminals at sizes their work asked for, spread across a canvas wider and taller than the window. Two are cut off at the edges, the left list marks the ones currently off-screen, and the minimap bottom-right frames the part being looked at](docs/img/palmar-light.png)

## What it does

**Terminals have a place, and keep it.** Pick a folder in the right rail, press *Open terminal
here*, and a shell opens on the canvas at that path. Drag the title bar to move it, the corner to
resize it — the shell's rows and columns follow, so a wider window is really a wider terminal.
The canvas grows as you add windows; scroll to reach the rest.

**Windows do not overlap.** Drop one on another and the one that was there slides out of the way — by
exactly the overlap, the shortest way out, carrying on to a third if it has to. Nothing is tiled and
nothing is resized: the sizes are yours, and the only thing palmar settles is the overlap. A line at
the bottom says how many moved and offers to undo it. **Push windows aside** in the shortcuts panel
turns it off — then windows may sit on top of each other, and clicking one brings it to the front.

**Canvases are tabs.** A strip above the canvas holds them. **＋** makes one and asks for its name;
double-click a tab to rename it, drag to reorder. A tab carries one small dot when something in
that canvas wants you, and nothing else. Every open browser sees the same tabs in the same order,
because the daemon owns them, not the page.

A canvas can be removed, **but only when it is empty** — the × appears on the tab you are on once
the last terminal there is closed, and not before. Removing a canvas must never end a process, so
there is no version of it that kills what is inside.

**The left list is never filtered by canvas.** Everything you have running is in it, wherever it
lives. It groups by canvas and folds, but folding can never hide the thing you are needed for: a
folded group still draws its waiting rows, and its header says how many of the sessions it hid are
asking for you. The order never changes — it is the tab order — so nothing moves under your hand. A row from another canvas
carries a small badge — click it and palmar switches to that canvas and brings you to the window.

**One terminal can fill the canvas.** The expand box blows a window up to the whole canvas, and its
rows and columns genuinely grow — this is not a camera. **Esc** brings you back. The rails stay
while you are zoomed in, because you still need to see who is waiting.

**Sessions outlive the browser.** The daemon owns the PTYs; the page is a client that attaches and
detaches. Close the tab, reopen the URL, and everything is where you left it. Closing a terminal in
the UI, on the other hand, really ends it — there is an **×** on the window and on the list row, and
each unfolds a confirmation in place rather than a native dialog. A second open browser drops the
window at the same moment.

**What to do next.** The lights say which terminal wants you; the right rail says which one first.
It lists what is waiting, **longest wait at the top**, because a terminal that has been asking for
twenty minutes is not the same as one that just asked. Below that it names terminals that claim to
be working but have printed nothing for a while — nothing else tells you that, and palmar can
because it owns the pty. When nothing wants you it says so, and shows what happened while you were
away instead.

**A minimap** sits bottom-right for the canvas you are on. Click or drag in it to move the viewport.

**Nothing moves a window unless you did something.** Dropping a window on another moves that one —
you aimed it there, it slides rather than jumps, and the undo is right there. What never happens is a
window moving because of something you did not do: closing the last terminal at the bottom shrinks the
canvas on its own, but closing the one at the top leaves the space above the rest, because taking that
space back means moving windows that are sitting in plain sight.
The button on the tab bar does it when you ask: everything slides back to the corner keeping its
spacing, and it greys out when there is nothing to close up. If you would rather it happened by
itself, **Tidy automatically** in the shortcuts panel turns that on.

**Copy and paste.** Select with the mouse, then `Ctrl+Shift+C` (`⌘C` on a Mac); paste with
`Ctrl+Shift+V` (`⌘V`), or the browser's own paste. **`Ctrl+C` is left alone** — in a terminal that
is the interrupt, and taking it away because something happened to be selected would stop the wrong
thing. The shortcut is shown once, the first time you select something.

**Search** with `Ctrl K` (`⌘K` on a Mac) reaches every session and folder by name, path, or agent.

**Text size, per terminal.** `Ctrl`/`⌘` + wheel over a terminal changes that one's text size. The
window keeps its size, so the rows and columns change with it — smaller text puts more of a log in
the same box, larger text makes one pane easy to read across the room. The size readout in its
title bar turns into the reset. `Ctrl −` still zooms everything at once, since that is the
browser's own.

## Install

palmar is the Python standard library and a vendored copy of xterm.js — there is nothing to build
and no dependency to fetch. So installing it is mostly a matter of putting it where your shell can
find it, and Python is the only thing you need already (macOS ships `/usr/bin/python3`; most Linux
has one). Either way is one line:

```
git clone https://github.com/maengyo/palmar && sh palmar/install.sh   # from a checkout
```

`install.sh` checks your Python, drops a `palmar` launcher in `~/.local/bin`, and changes nothing
else — no root, no `~/.palmar` touched, the daemon never started.

**On Windows** there is `install.ps1`, run with the policy bypassed for this one process — no
administrator, no setting changed:

```
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Check   # look; it writes nothing
powershell -ExecutionPolicy Bypass -File .\install.ps1          # then it asks before writing
```

**It does not give you a working palmar yet.** The daemon exits on Windows because `fcntl`, `pty`
and `termios` are not there — the native port is #29, and until it lands the launcher prints that.
What works today is the daemon inside WSL with any Windows browser pointed at the address it
prints. What `-Check` is good for now is the report: which Python is real (it skips the Microsoft
Store's zero-length `python.exe`), what the execution policy is in every scope, and whether WSL is
there. Prefer to read it first? `curl`
it to a file and run it by hand; it is short. Or skip it entirely and run the checkout directly:

```
python3 -m palmar
```

> A public one-line `curl … | sh` and a PyPI package (`uvx palmar`) are coming — the repository is
> private for now. When they land, `install.sh` already knows how to fetch from them: set
> `PALMAR_PYPI` or `PALMAR_TARBALL` and it will.

## Running it

```
palmar
```

It opens your browser and prints the address on its last line —
`http://127.0.0.1:8801/?k=…`. Bookmark it if you like: the key stays the same across restarts, so
the bookmark keeps working. Lost it? `cat ~/.palmar/run/url`. `--no-browser` prints and opens
nothing.

The key is what stops any other process on the machine from asking the daemon for the page and
reading your session token out of it.

There is nothing to install and nothing to build. palmar uses only the Python standard library, and
xterm.js is vendored in the repo, so no dependency is fetched — at install time or at runtime. It
binds `127.0.0.1` and nothing else.

Stop it with `Ctrl-C`, or from anywhere:

```
palmar --stop
```

That stops the daemon for your home **and the shells inside it**. It goes out the clean way, so the
workspace is saved and offered back the next time you start. Closing a browser tab or the app window
does not stop it — the daemon is what holds your terminals, which is why they survive.

### In a window instead

There is also a **741 KB** program that shows the same thing in its own window, with no browser
around it — see [`app/`](app/README.md). It is the system's own webview (WKWebView on macOS,
WebKitGTK on Linux and **WSLg**, WebView2 on Windows), so nothing is shipped twice, and it is not
Electron: being heavy is the thing this project is avoiding.

```
cd app && cargo build --release && ./target/release/palmar-app
```

**Rust is a build dependency, not a runtime one**, and there is somewhere to get a built one — CI
produces a Linux x86-64 binary and a macOS universal binary (Intel and Apple silicon in one file,
macOS 11 and up). See [`app/`](app/README.md) for which command downloads which.

**Pick whichever you prefer — you are not meant to run both.** But if you do, it works:
there is one daemon per home, whichever you start first brings it up, and the other attaches to it.
Run `palmar` while the window is already open and it opens the page on that same daemon instead of
refusing. A terminal opened in either shows up in the other, live. Closing either one leaves the
daemon — and your shells — running.

The window has been run on macOS and, since 2026-09-11, on WSLg. Setting WSLg up for the first
time — fonts, the input method, and what the failures look like — is written out in
[`docs/wslg.md`](docs/wslg.md) (Korean).

## Status lights, without configuring anything

A light is only worth having if it is right, and if it works for the agent you actually run.

**Agents already say when they are busy — in the window title.** They animate a spinner into it
while they think and drop it when they stop. palmar owns the PTY, so it sees that stream and reads
the state straight off it. Nothing to install, no config file, no permission to grant, and it works
for an agent palmar has never been taught about.

It does not keep a table of spinner characters, because every agent draws its own and any such
table would be wrong the day a new one appears. **It watches the churn instead**: a title that
changes twice in three seconds is a spinner by definition. Spinning is *working*; spinning that
stops is *done*; a title that never spun stays *idle*, so a text editor parked in a pane is not
mistaken for a finished job.

**An agent that sets no title at all still lights up.** palmar falls back to what is running and
whether it is printing: nothing running means idle, printing means working, and printing that stops
means it wants you. The first of those is what keeps an ordinary shell prompt dark. The honest limit
is an agent that thinks for a long time in complete silence — that is indistinguishable from finished.

**Where an agent offers hooks, palmar uses those instead**, because a hook is exact where a title
is inferred — it can tell an approval prompt from a finished turn. Hooks attach themselves: every
shell palmar opens gets a small shim in front of `PATH`, so there is still nothing for you to set
up. This works in zsh and bash.

**Notifications reach you when palmar does not have the screen.** The browser tab counts what wants
you, the favicon carries a dot, and the bell in the top bar turns on real notifications. It is off
until you turn it on, it asks for permission on that click and never on load, it fires only when a
terminal *becomes* one that wants you, several at once arrive as one notification, and clicking it
takes you to that terminal.

## Built on

| | |
|---|---|
| Daemon | Python standard library only — PTYs, HTTP, WebSocket, hooks. One file, ~2,500 lines. |
| Python | 3.9 and up. |
| Browser | Vanilla JavaScript, no framework, no build step. xterm.js draws the terminals. |
| Runs on | macOS and Linux. On Windows today, run it inside WSL and browse from Windows — native Windows is the next port, not a flag. |
| Network | **Nothing goes out.** The daemon has no HTTP client and the page loads nothing from anywhere — no CDN, no font host, no telemetry. It binds `127.0.0.1` to serve the page and that is the only socket it opens. |
| Security | A key gates the page, a token gates every API request (reads included), plus `Origin` and `Host` checks. Each terminal's pty is closed to every other terminal. **palmar does not isolate terminals from each other** — see below. |

## What palmar does not protect you from

Worth saying plainly, because the opposite is easy to assume.

**Anything running in a terminal can drive palmar.** Unix permissions separate *users*, not
programs: `0600` means "only you", not "only palmar", and the shells palmar opens run as you. So a
script or an agent in one pane can read the daemon's token and attach to any other pane — reading
its output and typing into it, including answering another agent's approval prompt. It can also
rewrite the hook shim, which sits at the front of every pane's `PATH`.

This is not a hole palmar opens. Anything running as you already has your SSH keys, your browser
cookies and your shell startup files. But palmar makes it easy and specific, and the approval
prompt is the part that matters: it is meant for a human to answer.

Closing it for real needs an OS boundary — a separate user or a container per pane — which would
make palmar a different program. It is written down rather than papered over.

**The key lives in the URL.** That is the cost of the bookmark surviving restarts. On a machine
where another account can run programs, that account can take the daemon's port while it is stopped
and be handed the key by a page that opens against it. The page never sends the key on its own for
exactly this reason — it asks you to reload instead — but opening the link yourself while an
impostor holds the port would hand it over. A desktop build will use a Unix socket, where there is
no port to take.

## What is not built yet

- **A new window does not push anything.** It lands in the first free slot instead, which on a canvas
  that grows without limit always exists. Pushing is what your own hand sets off, by dropping or
  resizing a window onto another.
- **No button moves a terminal between canvases.** The daemon does it and every open browser
  follows along — where the handle belongs on screen is not settled.
- **A daemon restart still ends every shell.** What comes back is where you were: the canvases
  return on their own, and the terminals are offered — each with the name you gave it and the folder
  it was in, including one you had `cd`-ed to. One click reopens them. The shells themselves cannot
  be handed to a new daemon, and palmar does not pretend otherwise.
- **fish shells do not get the hook shim.** Title-based status still works there.
- **A settings panel**, and a command palette behind the search box.
- **Native Windows.** The daemon is POSIX to the bone — `pty.fork`, `tcgetpgrp`, `flock`, signals,
  `0600` — and does not even import there. ConPTY reaches all of it through `ctypes`, so no
  dependency is needed, but it is a port of the daemon rather than a switch. WSL works meanwhile.

## How it works

```
┌─ left ────┬─ canvas ───────────────┬─ right ───┐
│ terminal  │ terminals sit where    │ directory │
│ list      │ you put them, at the   │ folders   │
│ waiting   │ size you gave them,    │ only,     │
│ on top    │ on the canvas you      │ read when │
│           │ tabbed to              │ expanded  │
└───────────┴───────────┬────────────┴───────────┘
                        │ WebSocket: bytes, status, canvases
┌───────────────────────┴────────────────────────┐
│  palmar daemon — owns the PTYs, reads status,  │
│  keeps the canvases. Alive with no browser.    │
└──────┬─────────────────────┬───────────────────┘
       │ PTY: bytes in and   │ hooks, per pane, when the agent has them
       │ out, resize         │
┌──────┴─────────────────────┴───────────────────┐
│  a pane is a shell. Run whatever you like in   │
│  it. Status is read from what it writes.       │
└────────────────────────────────────────────────┘
```

palmar opens shells and nothing else — it has no list of tools and no opinion about what you run.
The daemon holds the PTYs, the canvases, and the status; the browser holds where the windows sit.

The wire format between them is written down in [`docs/protocol.md`](docs/protocol.md): every
endpoint, both WebSockets, the flow control, the ring buffer, and how status is decided. The daemon
implements that document and nothing beyond it.

## Development

Run the daemon and open the printed URL; the browser files are served straight from `web/`, so a
reload picks up an edit. `dev/dev-stub.py` is a fake daemon that serves the same page with invented
sessions, for working on the UI without spawning real shells.

- [`AGENTS.md`](AGENTS.md) — the working agreement: how claims are checked, what goes in a
  measurement, and the rules for anything added to the repo.
- [`docs/protocol.md`](docs/protocol.md) — the contract between daemon and browser.
- [`docs/decisions.md`](docs/decisions.md) — what is settled, and what is deliberately still open.
- [`docs/roadmap.md`](docs/roadmap.md) — the order of work and what blocks what.

The living list is the [issues](https://github.com/maengyo/palmar/issues).

## License

palmar is MIT — see [`LICENSE`](LICENSE).

The only third-party code in this repo is xterm.js (`@xterm/xterm`, `@xterm/addon-fit`,
`@xterm/addon-webgl`) under `palmar/web/vendor/`, vendored so palmar fetches nothing at runtime. It is MIT
too; its notice is [`palmar/web/vendor/LICENSE-xterm`](palmar/web/vendor/LICENSE-xterm), kept beside the files
because the minified builds carry no header of their own.
