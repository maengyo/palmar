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

## Why a browser and not an app

The machine this has to work on is a locked-down Windows laptop with no admin rights,
where WSL is the only way in. A desktop app cannot be installed there. A browser pointed
at `localhost` can.

That constraint turns out to be a gift: the browser gives **real zoom**. `Ctrl+-` shrinks
the cells and more of the canvas fits — something no terminal UI can do, because a
terminal cannot scale text. Opened with `--app=`, a browser window has no chrome and
looks like an application anyway.

So there is one frontend, not three. herdr already ships a TUI for the terminal case.

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

## Prior art

[cate](https://github.com/0-AI-UG/cate) is where the spatial idea comes from, and it is
the better tool if you can install a desktop app. [herdr](https://herdr.dev) is the
runtime this is built on. Orca, Paseo, Emdash, Conductor and Superset all occupy this
space; none of them puts terminals on a free canvas in a browser.

## License

MIT
