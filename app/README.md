# palmar in its own window

`palmar-app` is a **741 KB** binary that shows palmar in a real window instead of a browser tab.

It is not a second copy of palmar. The daemon still owns every terminal; this program finds one
(or starts one), and points a webview at the address it prints. Closing the window leaves the
daemon — and your shells — running, exactly as closing a browser tab always did.

## Why not Electron, and why not the Tauri CLI

Being heavy is the thing this project is avoiding: `cate` ships a 496 MB dmg and installs 1.1 GB
(`docs/decisions.md`). Electron was ruled out for that reason, and Tauri was named as the answer
if a window were ever needed.

This is that answer with the parts we do not need taken out. There is no JavaScript to build and
no frontend to bundle — the UI is already served by the daemon over 127.0.0.1 — so what is left is
a window (`tao`) and a webview (`wry`), which is what Tauri is itself built on. No Node, no Tauri
CLI, no config file.

The webview is the one the operating system already has, so nothing is shipped twice:

| | webview | notes |
|---|---|---|
| macOS | WKWebView | in the OS; nothing to install |
| Linux / **WSLg** | WebKitGTK 4.1 | `libwebkit2gtk-4.1-0`, plus `-dev` to build |
| Windows | WebView2 | the native port is #29 — today, run the daemon in WSL |

## Build

Needs a Rust toolchain (`rustup`), nothing else.

```sh
cd app
cargo build --release
./target/release/palmar-app
```

### On WSL, for WSLg

WSLg draws Linux GUI programs on the Windows desktop, so this binary — built **inside** WSL — is a
normal Windows-looking window, the same way Obsidian is. The daemon is already POSIX and runs in
WSL unchanged.

**Check everything first.** From the top of a palmar checkout, inside WSL:

```sh
sh dev/wslg-probe.sh
```

It installs nothing and changes nothing; it prints a report and names the first thing that is
missing. Paste that report when something is wrong — it is the fastest way to tell "WSLg is not
working" apart from "palmar is not working". No key or token appears in it.

Then, if it is all clear:

```sh
sudo apt install -y libwebkit2gtk-4.1-dev build-essential pkg-config curl
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh   # if rustup is not there yet
cd app && cargo build --release
./target/release/palmar-app
```

**4.1, not 4.0.** This links `webkit2gtk` 2.0 / `soup3`, and `webkit2gtk-4.0` is the older libsoup2
API. Ubuntu 22.04+ and Debian 12+ have 4.1; Ubuntu 20.04 has only 4.0 and cannot build this.

**Clone inside WSL, not under `/mnt/c`.** A cargo build on the Windows filesystem is many times
slower, and the first one already fetches and compiles a few hundred crates.

#### If a window appears but is blank or black

That is WSLg's virtual GPU meeting WebKitGTK's compositing, not palmar. Try, in order:

```sh
WEBKIT_DISABLE_COMPOSITING_MODE=1 ./target/release/palmar-app
WEBKIT_DISABLE_DMABUF_RENDERER=1 ./target/release/palmar-app
```

If one of them fixes it, say which — it belongs in the program rather than in your shell, and
that is a decision to make once it is known which machines need it.

#### If no window appears at all

Check WSLg itself before palmar: `sudo apt install -y x11-apps && xeyes`. If that draws nothing,
nothing here can. `wsl --update` then `wsl --shutdown` from Windows, and open the distro again.

> **Not yet measured.** Everything in this section is what the code is written to do; it has not
> been run on a real WSLg machine. The macOS path in this file *has* been. See `docs/decisions.md`
> for what is measured and what is not.

## What it does, in order

1. reads `~/.palmar/run/url` and checks the port actually answers — a `kill -9` leaves that file
   behind, so the file alone is not evidence;
2. if nothing answers, runs `palmar --no-browser` and takes the address off stdout. That stdout's
   last line is the address is a contract (`docs/protocol.md`);
3. opens a window on it.

If it cannot reach a daemon it prints one sentence and exits. A window showing an error page would
be a worse version of that sentence.

## Licences

`tao` and `wry` are Apache-2.0/MIT, the same terms as palmar's MIT. Nothing is vendored here —
cargo fetches them — so there is no third-party notice to carry in this tree for this crate.
