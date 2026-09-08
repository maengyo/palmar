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

![palmar running: canvases as tabs, terminals placed on the canvas, the session that wants you at the top of the list](docs/img/palmar-light.png)

## What it does

**Terminals have a place, and keep it.** Pick a folder in the right rail, press *Open terminal
here*, and a shell opens on the canvas at that path. Drag the title bar to move it, the corner to
resize it — the shell's rows and columns follow, so a wider window is really a wider terminal.
The canvas grows as you add windows; scroll to reach the rest.

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

**Nothing moves a window except you.** Closing the last terminal at the bottom shrinks the canvas on
its own, but closing the one at the top leaves the space above the rest — because taking that space
back means moving windows, and a window you can see should not jump because a different one closed.
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

## Quick start

```
python3 -m palmar
```

It prints a URL on its last line — `http://127.0.0.1:8801/?k=…`. Open that, and bookmark it if
you like: the key stays the same across restarts, so the bookmark keeps working. Lost the URL?
`cat ~/.palmar/run/url`.

The key is what stops any other process on the machine from asking the daemon for the page and
reading your session token out of it.

There is nothing to install and nothing to build. palmar uses only the Python standard library, and
xterm.js is vendored in the repo, so no dependency is fetched — at install time or at runtime. It
binds `127.0.0.1` and nothing else.

Stop it with `Ctrl-C`.

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
| Daemon | Python standard library only — PTYs, HTTP, WebSocket, hooks. One file, ~1,700 lines. |
| Python | 3.9 and up. |
| Browser | Vanilla JavaScript, no framework, no build step. xterm.js draws the terminals. |
| Runs on | macOS, Linux, and WSL (run the daemon inside WSL, browse from Windows). |
| Network | **Nothing goes out.** The daemon has no HTTP client and the page loads nothing from anywhere — no CDN, no font host, no telemetry. It binds `127.0.0.1` to serve the page and that is the only socket it opens. |
| Security | A token in a `0600` file, plus `Origin` and `Host` checks, on every API request — reads included. Each terminal's pty is closed to every other terminal. |

## What is not built yet

- **Windows do not push each other aside.** A new one lands in the first free grid slot; dragging
  one onto another overlaps them.
- **No button moves a terminal between canvases.** The daemon does it and every open browser
  follows along — where the handle belongs on screen is not settled.
- **Nothing survives restarting the daemon** — not the canvases, not the names, not the shells.
- **fish shells do not get the hook shim.** Title-based status still works there.
- **A settings panel**, and a command palette behind the search box.

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
