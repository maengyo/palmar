# palmar

*Read this in [한국어](README.ko.md).*

**Many terminals, one place, and each one keeps where you put it.**

![palmar: three terminals on a canvas. Each carries a light — one working, one waiting for you, one finished. Two are dragged together into a named group and travel as one until a held Alt takes one back out, a PDF opens in a window of its own, and Ctrl with the wheel stands back far enough to see all of it](docs/img/palmar.gif)

> palmar runs no model, calls no API, and sends your work nowhere. It opens shells and shows you
> where they are. The agents are the ones you already run.

## Why

**You can see which one wants you.** Every terminal carries a light — working, waiting on you,
done, idle. There is nothing to install and nothing to configure: palmar attaches the agent's own
hooks itself — a shim in front of the shell on macOS and Linux, a preamble in PowerShell on Windows
— and where an agent offers none it reads the window title, and where there is no title either,
what the shell is actually printing. Your settings files are not touched. The one that has been
waiting longest sits at the top of the list, even if its window is off-screen.

**A window stays where you put it.** Drag it, size it, and the shell's rows and columns follow;
nothing is tiled and nothing is resized behind your back. Windows never overlap — drop one on
another and the other slides aside by exactly the overlap. Hold one over another and they become a
group that travels together, and a group can be given a name. One `Ctrl`/`⌘`+`Z` undoes any of it.

**And the canvas is bigger than the screen.** Two buttons put it back in order — one pulls the
windows to the corner, the other only closes the gaps and leaves the arrangement alone — and `Ctrl`
with the wheel over the bare canvas stands back far enough to see all of it. Standing back draws the
windows smaller; it never resizes a terminal, so nothing is told anything and nothing is measured
again.

**They keep running without you.** The daemon owns the shells, so closing the tab, or the laptop
lid, changes nothing. Come back to the same address and everything is where you left it — same
places, same sizes, same groups, because the arrangement lives with the daemon and not in one
browser.

**Your files are right there.** The rail beside the canvas is the folder you are working in. A
file opens in a window like any other — text with line numbers, a table for CSV, a page, a PDF —
and text can be edited and saved in place. If something else wrote the file meanwhile, palmar says
so instead of winning. Drag one in from Finder or Explorer and it opens where you let go; a button
on its title bar hands it to the program your machine would have opened it with.

**Nothing to install but Python.** The standard library and a vendored copy of xterm.js. No build
step, no packages, and no network at runtime — palmar opens no connection of its own unless you turn
on **Tell me about new versions** in the options list, which is off to begin with.

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
the web button inside it opens a browser, and so does `palmar --web`. Both at once is fine: one daemon
per home, and a terminal opened in either shows up in the other, live.

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
