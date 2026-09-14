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
resize it — the shell's rows and columns follow, so a wider window is really a wider terminal. The
canvas grows as you add windows; scroll, or use the minimap bottom-right, to reach the rest.

**Windows do not overlap.** Drop one on another and the one that was there slides out of the way —
by exactly the overlap, the shortest way out, carrying on to a third if it has to. Nothing is tiled
and nothing is resized: the sizes are yours, and the only thing palmar settles is the overlap.

**Nothing moves a window unless you did something.** A window you can see never jumps because a
different one closed; tidying up is a button, not a habit the canvas has.

**Windows that belong together can travel together.** Hold one still over another for a moment — the
one underneath lights up first — and they become a group: dragging any of them moves them all, and a
third can join the same way. `Alt`-drag takes one back out. A group is lighter than a canvas: a
canvas is a different workbench, a group is a set that lives together on one.

**Canvases are tabs.** The daemon owns them, so every open browser sees the same tabs in the same
order. A canvas can be removed only once it is empty — removing one must never end a process.

**The left list is never filtered by canvas.** Everything running is in it, wherever it lives,
waiting longest at the top. Folding a group can never hide the thing you are needed for. It also
names terminals that claim to be working but have printed nothing for a while — nothing else can
tell you that, and palmar can because it owns the pty.

**Sessions outlive the browser.** The daemon owns the PTYs; the page attaches and detaches. Close
the tab, reopen the address, and everything is where you left it. Closing a terminal *in the UI*
really ends it, and asks first.

## Install

palmar is the Python standard library and a vendored copy of xterm.js — nothing to build, nothing
to fetch. Python is the only thing you need already (macOS ships `/usr/bin/python3`; most Linux has
one):

```
git clone https://github.com/maengyo/palmar && sh palmar/install.sh
```

That drops a `palmar` launcher in `~/.local/bin` and changes nothing else — no root, no `~/.palmar`
touched, the daemon never started. Or skip it and run the checkout directly with `python3 -m palmar`.

> A public one-line `curl … | sh` and a PyPI package are coming — the repository is private for now.
> `install.sh` already knows how to fetch from them: set `PALMAR_PYPI` or `PALMAR_TARBALL`.

**Windows**: not yet natively — run the daemon inside WSL and browse from Windows. `install.ps1`
reports what a Windows machine has and installs the launcher for when the port lands; see
[`docs/windows.md`](docs/windows.md).

## Running it

```
palmar
```

It prints the address on its last line — `http://127.0.0.1:8801/?k=…` — opens your browser, and
**comes straight back**. The daemon goes into a session of its own, so closing the terminal does not
take it, or your shells, with it. The key stays the same across restarts, so a bookmark keeps
working; lost it, `cat ~/.palmar/run/url`. That key is what stops any other process on the machine
from asking the daemon for the page and reading your session token out of it.

Run `palmar` again and it hands you the same address rather than starting a second daemon.
`palmar --stop` ends it and the shells inside it, saving the workspace to be offered back next time.
`--foreground` keeps it attached to the terminal with `Ctrl-C`, and `--no-browser` opens nothing.

### In a window instead

There is also a **741 KB** program that shows the same thing in its own window — see
[`app/`](app/README.md). It uses the webview the OS already has (WKWebView, WebKitGTK, WebView2),
so nothing is shipped twice, and it is not Electron. CI builds a Linux x86-64 binary and a macOS
universal one, so Rust is a build dependency and not a runtime one.

**Pick whichever you prefer.** Both at once also works: one daemon per home, whichever starts first
brings it up, and a terminal opened in either shows up in the other, live.

## Status lights, without configuring anything

A light is only worth having if it is right, and if it works for the agent you actually run.

**Agents already say when they are busy — in the window title.** They animate a spinner into it
while they think and drop it when they stop. palmar owns the PTY, so it reads that straight off the
stream: nothing to install, no config file, and it works for an agent palmar has never been taught
about. It keeps no table of spinner characters, because any such table is wrong the day a new agent
appears — **it watches the churn instead**: a title that changes twice in three seconds is a
spinner by definition. Spinning is *working*; spinning that stops is *done*; a title that never
spun stays *idle*, so an editor parked in a pane is not mistaken for a finished job.

**An agent that sets no title still lights up**, from what is running and whether it is printing.
The honest limit is an agent that thinks for a long time in complete silence — indistinguishable
from finished.

**Where an agent offers hooks, palmar uses those instead**, because a hook can tell an approval
prompt from a finished turn. They attach themselves: every shell palmar opens gets a small shim in
front of `PATH`, so there is still nothing to set up. zsh and bash.

**Notifications** are off until you turn on the bell in the top bar. They fire only when a terminal
*becomes* one that wants you, arrive as one when several do at once, and take you there on a click.

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

- **A new window does not push anything.** It lands in the first free slot, which on a canvas that
  grows without limit always exists. Pushing is what your own hand sets off.
- **No button moves a terminal between canvases.** The daemon can; where the handle belongs on
  screen is not settled.
- **A daemon restart still ends every shell.** What comes back is where you were — canvases return
  on their own, terminals are offered with their name and folder, one click reopens them. The shells
  themselves cannot be handed over, and palmar does not pretend otherwise.
- **fish shells do not get the hook shim.** Title-based status still works there.
- **A settings panel**, and a command palette behind the search box.
- **Native Windows.** The daemon is POSIX to the bone — `pty.fork`, `tcgetpgrp`, `flock`, signals —
  so it is a port rather than a switch. ConPTY reaches all of it through `ctypes`, so no dependency
  is needed. WSL works meanwhile; progress is in [`docs/windows.md`](docs/windows.md).

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

palmar opens shells and nothing else — no list of tools, no opinion about what you run. The wire
format between the two halves is written down in [`docs/protocol.md`](docs/protocol.md), and the
daemon implements that document and nothing beyond it.

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
