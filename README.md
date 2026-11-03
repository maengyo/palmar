# palmar

*Read this in [한국어](README.ko.md).*

**Many terminals, one place, and each one keeps where you put it.**

![palmar: three terminals on a canvas. Each carries a light — one working, one waiting for you, one finished. Two are dragged together into a named group and travel as one until a held Alt takes one back out, a PDF opens in a window of its own, Ctrl with the wheel stands back far enough to see all of it, a second canvas opens and the tabs switch between them, the left rail folds to a strip and comes back, and a terminal says something — a card by the bell, then the bell's list with the address already a link](docs/img/palmar.gif)

> palmar runs no model, calls no API, and sends your work nowhere. It opens shells and shows you
> where they are. The agents are the ones you already run.

## What it does

- **A light on every terminal** — working, waiting on you, done, idle. The one that has waited
  longest sits at the top of the list, even if its window is off-screen.
- **Windows stay where you put them.** Drag, resize, and the shell's rows and columns follow.
  Nothing is tiled. Drop one on another and the other slides aside by exactly the overlap.
- **Groups.** Hold one window over another and they travel together; give the group a name;
  `Alt`-drag takes one back out. `Ctrl`/`⌘`+`Z` undoes any of it.
- **A canvas bigger than the screen**, with canvases side by side as tabs. Two buttons tidy it;
  `Ctrl` with the wheel stands back to see all of it, or in to 2×, without resizing a terminal.
- **The shells outlive the tab.** A daemon owns them: close the browser, close the lid, come back
  to the same address and everything is where you left it.
- **Files beside the terminals.** The folder rail opens text (editable, saved in place), CSV as a
  table, pages, PDFs — in windows like any other. Drag a file in from Finder or Explorer.
- **Messages from terminals.** `palmar --message "review requested …"` from a script shows a card
  by the bell; the bell keeps the last hundred, with addresses already links. Notifications when
  the window is not in front, if you turn them on.
- **Every command has a key**, and `Ctrl`/`⌘`+`K` lists them all with the terminals that match.

## Why palmar

- **Nothing to install but Python**, and no configuration. The standard library and a vendored
  xterm.js; no build step, no packages, no network at runtime (the new-version check is off by
  default).
- **It attaches to the agents you already run** — Claude Code, Codex, whatever comes next. palmar
  has no tool list: it opens a shell, and you decide what runs in it. Hooks are attached by a shim
  in front of the shell (a PowerShell preamble on Windows); your settings files are never touched.
- **Light.** No Electron. The page is one HTML file; the daemon is one Python process that owns the
  PTYs and moves bytes. Terminal emulation stays in the browser.
- **Local only.** Bound to `127.0.0.1`, a token on every request, Origin and Host checked.
  Nothing leaves the machine.
- **macOS, Linux, Windows** — the same page everywhere, and a window of its own on each.

## Install

One line. Python is the only thing it needs, and if there is none the installer gets one — with your
package manager on Linux (it asks first), with winget on Windows; macOS has `/usr/bin/python3`.

macOS · Linux:

```
curl -fsSL https://raw.githubusercontent.com/maengyo/palmar/main/install.sh | sh
```

Windows, in PowerShell or cmd — it fetches the script to the current folder and runs it under
`-ExecutionPolicy Bypass`, so no policy is changed and the file is there to read afterwards:

```
powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/maengyo/palmar/main/install.ps1 -OutFile install.ps1; .\install.ps1"
```

To read it before it runs, or to pass `-Check` / `-Prefix` / `-Yes`, split that in two: the `irm … -OutFile`
half, then `powershell -ExecutionPolicy Bypass -File .\install.ps1`.

## Updating

```
palmar --update
```

It fetches the latest release's tree, checks it against the release's `SHA256SUMS`, stops the
daemon and starts it again — in that order, which is the part that is easy to get wrong by hand.
(The installers fetch the same way. A tree that is not a release — a branch, main's head — is
refused: there is no `SHA256SUMS` beside it.) It says what it is about to do first, and the shells in your
open terminals close with the daemon, so it lists them and what each was running before it asks.
They come back as an offer, in the same folders; an agent comes back through its own `--resume`.

`--yes` answers the question in advance. **If you installed by cloning, it says so and stops** —
there `git pull` is the update, and `--update` would throw your working tree away.

`palmar --uninstall` is the other direction: it removes the tree, the launcher, the shortcuts and
the PATH line the installer added, after saying so and asking. `~/.palmar` stays — your layout,
settings and the restore offer — unless you add `--purge`.

It downloads the tree, drops a `palmar` launcher (`~/.local/bin`, or `%LOCALAPPDATA%\palmar\bin`),
puts that on your PATH once, and starts nothing. No root, no build. Prefer to read first?
`git clone https://github.com/maengyo/palmar && sh palmar/install.sh` installs the checkout you can
see, and `python3 -m palmar` runs one with nothing installed at all. Windows is native (ConPTY);
what is still rough there is in [`docs/windows.md`](docs/windows.md).

## Running it

```
palmar
```

It prints its address, opens a window and comes straight back. On a Mac the window is palmar's own
when it is installed; elsewhere it is a Chromium-family browser in app mode (your default browser when
it is one; Windows always has Edge), and palmar's own window is for a Linux without one. A browser tab
is the last resort. To make
it an app of its own with no exe, install it once from Edge or Chrome (menu → Apps → Install palmar):
it gets its own icon, window and Start Menu entry, and `palmar` opens that from then on — the daemon detaches, so closing the terminal does not take your shells with it. Run `palmar` again and the open window comes to the
front — same daemon, same address; `palmar --new` opens another window, `palmar --stop` ends it. Lost the address: `cat ~/.palmar/run/url`.
If 8801 is busy — a palmar on Windows beside one in WSL, say — it takes the next free port and says so.

The window is a **712 KB** program that shows the same thing without a browser
([`app/`](app/README.md)) — the OS's own webview, not Electron. (That is the Linux download; the
macOS one is 1.5 MB because the single file carries both Intel and Apple Silicon.) `palmar` opens it when it is there;
*Open in a browser* in its options list opens a browser, and so does `palmar --web`. Both at once is fine: one daemon
per home, and a terminal opened in either shows up in the other, live.

**Something running in a terminal can say something.** `palmar --message "build done"` puts that
line on the terminal's row in the left list, shows it as a card by the bell for twenty seconds —
then it shrinks into a dot and flies into the bell — and sends a notification if you are elsewhere
and have turned them on. Several stack; press a card to open the bell, or its × to close just that
one (it stays in the bell). Lines are kept (`palmar --message -` reads them from stdin), and the row
shows the first. It stays on the row until you click the
terminal, and the bell keeps the last hundred — with how long ago, an × on each, and the
notification switch at the top. It needs no id — it knows which terminal it was typed in — so a script that watches
something can call it in a loop; from outside a terminal, name one with `--to <id>` (`palmar --list`).

## Worth knowing

**Anything running in a terminal can drive palmar.** Unix permissions separate users, not programs:
the shells palmar opens run as you, so a script in one pane can attach to another and answer its
approval prompt. Anything running as you already has your SSH keys and your shell startup files —
but palmar makes it specific, and the approval prompt is the part that is meant for a human. Closing
it for real needs an OS boundary per pane, which would make palmar a different program.

**The key lives in the URL**, which is what lets a bookmark survive a restart. The full cost, and
the rest of what is not built yet, is in [`docs/decisions.md`](docs/decisions.md).

## The name

*Palmar* means "of the palm of the hand" — every terminal in the palm of your hand. It was *palmer*
for a week, which is a surname, a letter off from what it meant, and taken on PyPI; *palmar* was
free everywhere.

## The rest

- [`AGENTS.md`](AGENTS.md) — the working agreement: how a claim gets checked before it is written down.
- [`docs/protocol.md`](docs/protocol.md) — the contract between daemon and browser.
- [`docs/decisions.md`](docs/decisions.md) — what is settled, and what is deliberately still open.
- [`docs/roadmap.md`](docs/roadmap.md) — the order of work.
- [`docs/reports.md`](docs/reports.md) — what was reported, what it turned out to be, and what is
  still open. Including the ones that could not be reproduced, which is most of what a list like
  this is for.

The living list is the [issues](https://github.com/maengyo/palmar/issues).

## License

MIT — see [`LICENSE`](LICENSE). The only third-party code here is xterm.js under
`palmar/web/vendor/`, vendored so palmar fetches nothing at runtime; its notice sits beside it in
[`LICENSE-xterm`](palmar/web/vendor/LICENSE-xterm).
