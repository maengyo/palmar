# palmer

> ⚠️ Day one. Nothing is built yet — this is the plan and what has been verified.

A spatial canvas for the coding agents you have running. Terminals sit where you put
them, at the size you chose, and each one shows whether it is working, waiting on you,
or done.

palmer is **not a terminal multiplexer**. [herdr](https://github.com/herdrdev/herdr)
already is one, and a good one: a Rust daemon that keeps agents alive across
disconnects and restarts, tracks their state, and exposes all of it over a local socket.
palmer is a **client** — the part herdr deliberately leaves to you.

```
┌─ your browser ────────────────┐
│  palmer — canvas, zoom, drag  │
└──────────────┬────────────────┘
               │ websocket
┌──────────────┴────────────────┐
│  palmer server (thin bridge)  │
└──────────────┬────────────────┘
               │ ~/.config/herdr/herdr.sock  (newline-JSON)
┌──────────────┴────────────────┐
│  herdr — terminals, agents,   │
│  persistence, status          │
└───────────────────────────────┘
```

## Why this exists

[cate](https://github.com/0-AI-UG/cate) already puts terminals on a zoomable canvas, and
it is excellent. It is also an IDE: editor, browser, terminal, and its own runtime
underneath. palmer is the narrow version — **terminals only, on somebody else's runtime**.

The bet is that narrow is worth something. Orca and Paseo are widely called heavy, and
the usual reason is surface area: desktop plus mobile plus web plus CLI needs a frame,
and the frame is the weight. palmer keeps one frontend and borrows the hard part.

**Be honest about how strong that is.** It is a preference for focus, not a capability
nobody else has. What is genuinely unoccupied is narrower: people build clients on herdr
— a macOS console, a menu-bar remote, a review sidebar — and none of them is a canvas.

## One frontend, in a browser

The browser is the default, and the reason is zoom. `Ctrl+-` shrinks the cells and more
of the canvas fits; no terminal UI can do that, because a terminal cannot scale text.
Opened with `--app=`, a browser window has no chrome and reads as an application.

If it later deserves a real window, the same web code goes inside Tauri for a few MB.
Electron is the thing being complained about; it is not the answer here.

There is no CLI frontend. herdr already ships a TUI, and a second one would be a worse
copy.

## What is verified

Measured against herdr 0.8.2 before writing any code:

- installs to `~/.local/bin` with **no sudo** — 18MB, single binary, checksum-verified
- `herdr server` runs headless; the socket appears at `~/.config/herdr/herdr.sock`
- the socket speaks newline-delimited JSON, protocol 20, **91 methods**
- `session.snapshot` returns workspaces, tabs, panes, layouts and agents — and each pane
  carries `agent_status`, which is the traffic light
- `pane.send_text` types into a terminal; `pane.read` reads it back
- **`pane.read` with `format: "ansi"` preserves escape sequences**, so pane output can be
  fed straight to xterm.js

That last one is the load-bearing fact. Without it there is no browser terminal.

## What is not verified

- whether output can be **streamed** rather than polled — `events.subscribe` and
  `pane.wait_for_output` exist, but a live terminal needs more than snapshots
- whether herdr installs on the target WSL box without admin ("WSL beta,
  endpoint-protected install" in their docs)
- how `agent_status` behaves with a real agent — it read `unknown` for a plain shell

## Where to start

Three questions decide whether this is buildable, and they come before any code:

1. [#1](https://github.com/maengyo/palmer/issues/1) — can pane output be **streamed**, or
   only polled? The answer shapes the whole server.
2. [#2](https://github.com/maengyo/palmer/issues/2) — does herdr install on the target
   WSL box **without admin**? If not, palmer has no reason to exist.
3. [#3](https://github.com/maengyo/palmer/issues/3) — is `agent_status` trustworthy with
   a real agent, and does it tell **blocked** from **working**?

Work is tracked in [issues](https://github.com/maengyo/palmer/issues) and on the
[board](https://github.com/users/maengyo/projects/3). `AGENTS.md` is the working
agreement; `docs/herdr-api.md` is what the API actually does, measured.

## Prior art

[cate](https://github.com/0-AI-UG/cate) is where the spatial idea comes from, and it is
the better tool if you can install a desktop app. [herdr](https://herdr.dev) is the
runtime this is built on. Orca, Paseo, Emdash, Conductor and Superset all occupy this
space; none of them puts terminals on a free canvas in a browser.

## License

MIT
