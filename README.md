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
canvas with something waiting floats to the top, a folded group still draws its waiting rows, and
its header says how many of the sessions it hid are asking for you. A row from another canvas
carries a small badge — click it and palmar switches to that canvas and brings you to the window.

**One terminal can fill the canvas.** The expand box blows a window up to the whole canvas, and its
rows and columns genuinely grow — this is not a camera. **Esc** brings you back. The rails stay
while you are zoomed in, because you still need to see who is waiting.

**Sessions outlive the browser.** The daemon owns the PTYs; the page is a client that attaches and
detaches. Close the tab, reopen the URL, and everything is where you left it. Closing a terminal in
the UI, on the other hand, really ends it — there is an **×** on the window and on the list row, and
each unfolds a confirmation in place rather than a native dialog. A second open browser drops the
window at the same moment.

**A minimap** sits bottom-right for the canvas you are on. Click or drag in it to move the viewport.

**Search** with `Ctrl K` (`⌘K` on a Mac) reaches every session and folder by name, path, or agent.

**`Ctrl −` gives you more canvas.** It is the browser's own zoom, so the cell shrinks and every
terminal gets more rows and columns at once.

## Quick start

```
python3 -m palmar
```

It prints `http://127.0.0.1:8801` on its last line. Open that.

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
| Security | A token in a `0600` file, plus `Origin` and `Host` checks, on every request that changes anything. |

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
