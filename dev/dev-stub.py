#!/usr/bin/env python3
"""dev-stub — a fake daemon for development. **This is not the product.** The product is palmar/daemon.py.

Imitates only the surface of docs/protocol.md so the browser side (web/) can be run without the daemon:
    GET  /, /app.js …            static files from web/. Plants window.PALMAR_TOKEN into index.html
    WS   /events?token=          two fake sessions in hello. On seen: done → idle
    WS   /pty/<id>?token=…       hello frame + ring buffer replay + **keys are echoed straight back**. There is no PTY
    GET  /api/sessions           the list — **not filtered by canvas** (the left list sees them all)
    POST /api/sessions?token=    makes one more fake session (no shell starts). Takes cwd·canvas·name
    PATCH /api/sessions/<id>     rename · move canvas (⑫ ⑪)
    DELETE /api/sessions/<id>    removes it (--delay-gone can delay just the `gone` broadcast — DELAY_GONE below)
    GET  /api/canvases           the list, in order                 (⑪)
    POST /api/canvases?token=    appends one at the end
    POST /api/canvases/order     the whole current set in a new order (409 signals the drift)
    PATCH /api/canvases/<id>     rename
    DELETE /api/canvases/<id>    empty ones only · never the last one (both 409) — PROVISIONAL in protocol.md
    GET  /api/dirs[?path=]       reads **the real filesystem, read-only** — folders only, no dots, branch from .git/HEAD
    POST /api/dirs               501 — this stub creates nothing
    POST /hook/claude?pane=      hook_event_name from the hook JSON → status, per protocol.md's table (curl status tests)

Standard library only, Python 3.9. The websocket framing comes from spike D (docs/spikes/2026-09-07/pipeline/py/server.py).
Binds only to 127.0.0.1, checks Origin·Host·token, and puts X-Frame-Options·frame-ancestors on every response
(protocol.md "인증", verbatim) — even a stub will not leave a shell imitation open to any site.
"""

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import os
import secrets
import struct
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

WS_MAGIC = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
WEB = (Path(__file__).resolve().parent.parent / "palmar" / "web").resolve()

# The stub stands in the daemon's place too (protocol.md) — its version must match the real one.
# **Do not copy the number out**: a copy makes the stub lie quietly when only one side is bumped.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from palmar import PROTOCOL
#: Same value as the product (palmar/daemon.py) — a looser stub grows a UI that only works here.
MAX_BODY = 1024 * 1024
REQUEST_TIMEOUT = 10
TOKEN = secrets.token_urlsafe(32)
#: The right to fetch the page (#14). Same rule as the product — a looser stub grows a UI that only works here.
#: The product leaves it in ~/.palmar/run/key; the stub invents its sessions, so it is new every run.
KEY = secrets.token_urlsafe(32)
PORT = [8801]
HOME = Path.home().resolve()
DEBUG = [False]
# --delay-gone: after 204 on DELETE, delay the `gone` broadcast by this much (seconds). 0 = at once, as now.
# A handle for watching whether the browser **keeps the row and the tile until gone arrives after DELETE**
# (protocol.md says only "지우는 것은 gone", nothing about the gap — and without the gap you cannot see it).
DELAY_GONE = [0.0]


def log(*a):
    if DEBUG[0]:
        print(time.strftime("%H:%M:%S"), *a, file=sys.stderr, flush=True)

# hook event → status (the protocol.md "상태" table)
HOOK_STATUS = {
    "SessionStart": "idle", "UserPromptSubmit": "working", "PermissionRequest": "waiting",
    "Stop": "done", "SessionEnd": "unknown",
}


# ── websocket framing (straight from spike D) ───────────────
class Frame:
    @staticmethod
    def build(payload, opcode=0x2):
        n = len(payload)
        if n < 126:
            head = struct.pack("!BB", 0x80 | opcode, n)
        elif n < 65536:
            head = struct.pack("!BBH", 0x80 | opcode, 126, n)
        else:
            head = struct.pack("!BBQ", 0x80 | opcode, 127, n)
        return head + payload

    @staticmethod
    def text(obj):
        return Frame.build(json.dumps(obj).encode(), 0x1)


async def read_frame(reader):
    head = await reader.readexactly(2)
    opcode = head[0] & 0x0F
    masked = head[1] & 0x80
    n = head[1] & 0x7F
    if n == 126:
        n = struct.unpack("!H", await reader.readexactly(2))[0]
    elif n == 127:
        n = struct.unpack("!Q", await reader.readexactly(8))[0]
    mask = await reader.readexactly(4) if masked else b""
    payload = await reader.readexactly(n) if n else b""
    if masked:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return opcode, payload


# ── canvases (⑪) ───────────────────────────────────────────
class Canvas:
    _seq = 0

    def __init__(self, name=None):
        self.id = secrets.token_urlsafe(16)      # same shape as a session id (protocol.md "캔버스")
        self.name = name
        self.order = 0
        Canvas._seq += 1
        self.seq = Canvas._seq                   # creation order. The label is built from this (protocol.md "캔버스")

    def json(self):
        return {"id": self.id, "name": self.name, "order": self.order, "seq": self.seq}


CANVASES = []   # in order. Renumbered from 0 with no gaps


def renumber():
    for i, c in enumerate(CANVASES):
        c.order = i


def canvas_by_id(cid):
    for c in CANVASES:
        if c.id == cid:
            return c
    return None


def clean_name(v):
    """protocol.md "이름 규칙": if a string, strip the surrounding whitespace, 1–64 chars, no control characters.

    Returns (ok, name). null · the empty string · whitespace-only are all treated alike as no name (None).
    """
    if v is None:
        return True, None
    if not isinstance(v, str):
        return False, None
    t = v.strip()
    if not t:
        return True, None
    if len(t) > 64:
        return False, None
    for ch in t:
        if ord(ch) < 0x20 or ord(ch) == 0x7F:
            return False, None
    return True, t


# ── fake sessions ───────────────────────────────────────────
class Sess:
    def __init__(self, cwd, status="unknown", agent=None, last_event=None, banner=b"",
                 canvas=None, name=None):
        self.id = secrets.token_urlsafe(16)          # 22 chars, unguessable (protocol.md Session.id)
        self.cwd = cwd
        self.canvas = canvas                         # ⑪ never null — a session is always somewhere
        self.name = name                             # ⑫ the human-given name. If None the browser makes a label
        self.cols, self.rows = 80, 24
        self.status, self.agent, self.alt = status, agent, False
        self.created = time.time()
        self.last_event = last_event
        self.buf = bytearray(banner)                # a fake ring buffer — nothing is trimmed here
        self.watchers = set()                       # the writers attached over /pty

    def json(self):
        return {"id": self.id, "cwd": self.cwd, "cols": self.cols, "rows": self.rows, "status": self.status,
                "agent": self.agent, "alt": self.alt, "created": self.created, "last_event": self.last_event,
                "canvas": self.canvas, "name": self.name}

    def emit(self, data):
        self.buf += data
        for w in list(self.watchers):
            try:
                w.write(Frame.build(data))
            except Exception:
                self.watchers.discard(w)


SESSIONS = {}
EVENT_CLIENTS = set()


def broadcast(obj):
    frame = Frame.text(obj)
    for w in list(EVENT_CLIENTS):
        try:
            w.write(frame)
        except Exception:
            EVENT_CLIENTS.discard(w)


def seed():
    """Scatters five sessions across three canvases.

    The browser opens on the canvas whose order is first, so **one of the waiting ones sits on the second
    canvas** — that is what makes "a dot lights on another canvas's tab, and it comes to the top of the
    left list wearing a badge" something you can see.
    """
    c1, c2, c3 = Canvas("api"), Canvas(), Canvas("scratch")   # c2 has no name → the browser makes a label
    CANVASES.extend([c1, c2, c3])
    renumber()

    # **The checkout this script is in**, not somebody's own directory. It used to name one
    # developer's folder layout, which meant it only worked on that machine — and put that layout
    # in a public repository (2026-09-17).
    proj = Path(__file__).resolve().parent.parent
    a_cwd = str(proj) if proj.is_dir() else str(HOME)
    a = Sess(a_cwd, status="waiting", agent="claude", last_event="PermissionRequest",
             canvas=c2.id, banner=(
        "\x1b[2mdev-stub: fake pane — keys are echoed, nothing runs\x1b[0m\r\n"
        "\r\n\x1b[2m⏺\x1b[0m Bash(ls /usr/bin | wc -l)\r\n"
        "  ⎿  Running…\r\n\r\n"
        "╭──────────────────────────────────────────────╮\r\n"
        "│ Bash command                                 │\r\n"
        "│   ls /usr/bin | wc -l                        │\r\n"
        "│ Do you want to proceed?                      │\r\n"
        "│ ❯ 1. Yes                                     │\r\n"
        "│   2. No                                      │\r\n"
        "╰──────────────────────────────────────────────╯\r\n$ ").encode())
    b = Sess(a_cwd, status="working", agent="claude", last_event="UserPromptSubmit", canvas=c1.id,
             name="auth refactor",      # ⑫ one with a name — the label beats the path
             banner=b"dev-stub: fake pane (echo only)\r\n$ claude\r\n")
    c = Sess(str(HOME), canvas=c1.id, banner=b"dev-stub: fake shell (echo only)\r\n$ ")
    d = Sess(a_cwd, status="done", agent="claude", last_event="Stop", canvas=c3.id,
             banner=b"dev-stub: fake pane (echo only)\r\n$ ")
    e = Sess(str(HOME), canvas=c3.id, name="notes", banner=b"dev-stub: fake shell (echo only)\r\n$ ")
    for x in (a, b, c, d, e):
        SESSIONS[x.id] = x


# ── directories (read-only) ──────────────────────────────────
def owned_by_me(p) -> bool:
    """Is the resolve()d path owned by the current uid? The same predicate as palmard.owned_by_me (#31)."""
    try:
        return os.stat(str(p)).st_uid == os.getuid()
    except OSError:
        return False


def roots():
    """The user home + the /Users/* /home/* **that I own**.

    This stub reads the real filesystem at `/api/dirs`, so its root rule has to match the real one
    (protocol.md "뿌리(roots)": whatever stands in the daemon's place keeps the same rule). Without the uid check,
    a stub started as `aa` under WSL shows `/home/bb` in the directory rail as-is — exactly the spot #31 ②
    fixed in the daemon. Ownership is measured on the **resolve()d path**, the same as palmard."""
    out = [HOME]
    for base in (Path("/Users"), Path("/home")):
        if base.is_dir():
            try:
                for p in sorted(base.iterdir()):
                    r = p.resolve()
                    if (r != HOME and p.is_dir() and not p.name.startswith(".")
                            and owned_by_me(r) and os.access(r, os.R_OK | os.X_OK)):
                        out.append(r)
            except OSError:
                pass
    return out


def in_roots(p):
    return any(p == r or p.is_relative_to(r) for r in roots())


def git_branch(d):
    """Reads one line of <dir>/.git/HEAD. If .git is a file (worktree), follows gitdir:. None if unreadable."""
    try:
        g = d / ".git"
        if g.is_file():
            line = g.read_text().strip()
            if not line.startswith("gitdir:"):
                return None
            g = Path(line[7:].strip())
            if not g.is_absolute():
                g = (d / g).resolve()
        head = (g / "HEAD").read_text().strip()
        if head.startswith("ref: refs/heads/"):
            return head[len("ref: refs/heads/"):]
        return head[:8] if head else None
    except OSError:
        return None


def has_subdir(d):
    try:
        with os.scandir(d) as it:
            for e in it:
                if not e.name.startswith(".") and e.is_dir(follow_symlinks=False):
                    return True
    except OSError:
        pass
    return False


def list_dirs(path):
    if path is None:
        # the root list: path stays empty and name carries the absolute path (protocol.md does not pin this down)
        return {"path": None, "entries": [
            {"name": str(r), "git_branch": git_branch(r), "has_children": has_subdir(r)} for r in roots()]}
    p = Path(path).expanduser().resolve()
    if not p.is_dir() or not in_roots(p):
        return None
    entries = []
    try:
        with os.scandir(p) as it:
            for e in sorted(it, key=lambda e: e.name):
                if e.name.startswith(".") or not e.is_dir(follow_symlinks=False):
                    continue
                d = p / e.name
                entries.append({"name": e.name, "git_branch": git_branch(d), "has_children": has_subdir(d)})
    except OSError:
        return None
    return {"path": str(p), "entries": entries}


# ── HTTP helpers ────────────────────────────────────────────
def respond(writer, status, body=b"", ctype="application/json; charset=utf-8", extra=""):
    reason = {200: "OK", 201: "Created", 204: "No Content", 400: "Bad Request", 403: "Forbidden",
              404: "Not Found", 405: "Method Not Allowed", 409: "Conflict",
              501: "Not Implemented"}.get(status, "OK")
    # protocol.md "인증": on every HTTP response. The same two lines as the product
    # (palmar/daemon.py http()) — the stub plants a token in index.html too, so being wrapped in an iframe
    # costs it exactly what it costs the product (#10).
    head = (f"HTTP/1.1 {status} {reason}\r\nContent-Type: {ctype}\r\nContent-Length: {len(body)}\r\n"
            f"Cache-Control: no-store\r\n"
            f"X-Frame-Options: DENY\r\nContent-Security-Policy: frame-ancestors 'none'\r\n{extra}\r\n")
    log("  ->", status)
    writer.write(head.encode() + body)


def jbody(obj):
    return json.dumps(obj).encode()


async def handle(reader, writer):
    try:
        request = await reader.readuntil(b"\r\n\r\n")
    except Exception:
        writer.close()
        return
    lines = request.decode("latin-1").split("\r\n")
    try:
        method, target, _ = lines[0].split(" ", 2)
    except ValueError:
        writer.close()
        return
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    url = urlparse(target)
    q = parse_qs(url.query)
    path = unquote(url.path)
    log(method, path, "origin=" + headers.get("origin", "-"), "upgrade=" + headers.get("upgrade", "-"))

    def allowed_origin():
        o = headers.get("origin")
        return o is None or o in (f"http://127.0.0.1:{PORT[0]}", f"http://localhost:{PORT[0]}")

    def allowed_host():
        # protocol.md "인증": Host may only be those same two. It blocks the path where DNS
        # rebinding (attacker domain → 127.0.0.1) reads the token out of index.html. Same rule as the
        # product's palmard.allowed_host() — the stub plants its token in the same place, so if only this
        # is left open the stub becomes that path (measured 2026-09-08: GET / with Host: evil.example
        # returned 200 and PALMAR_TOKEN was sitting in the body).
        h = headers.get("host")
        return h is None or h in (f"127.0.0.1:{PORT[0]}", f"localhost:{PORT[0]}")

    def has_token():
        got = q.get("token", [""])[0]
        ok = hmac.compare_digest(got, TOKEN)
        if not ok:
            log("  token mismatch: got", len(got), "chars, query keys", sorted(q))
        return ok

    async def body_json():
        # **The same cap and deadline as the product.** Trust `Content-Length` and hand it to `readexactly`
        # and one unauthenticated request can declare 1TiB and eat memory, or send no body and hold the
        # connection (Codex review 2026-09-09: a declared 1TiB went straight through to `readexactly`).
        try:
            n = int(headers.get("content-length", "0") or 0)
        except ValueError:
            return {}
        if n < 0 or n > MAX_BODY:
            return {}
        try:
            raw = await asyncio.wait_for(reader.readexactly(n), REQUEST_TIMEOUT) if n else b"{}"
        except (asyncio.TimeoutError, asyncio.IncompleteReadError):
            return {}
        try:
            return json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return {}

    async def finish():
        try:
            await writer.drain()
        except Exception:
            pass
        writer.close()

    if not allowed_origin():
        respond(writer, 403, jbody({"error": "bad origin"}))
        await finish()
        return
    if not allowed_host():
        respond(writer, 403, jbody({"error": "bad host"}))
        await finish()
        return

    # ── websocket ──
    if headers.get("upgrade", "").lower() == "websocket":
        if not has_token():
            respond(writer, 403, jbody({"error": "bad token"}))
            await finish()
            return
        sess = None
        if path.startswith("/pty/"):
            sess = SESSIONS.get(path[5:])
            if sess is None:
                respond(writer, 404, jbody({"error": "no such session"}))
                await finish()
                return
        elif path != "/events":
            respond(writer, 404, jbody({"error": "no such socket"}))
            await finish()
            return
        key = headers.get("sec-websocket-key", "").encode()
        accept = base64.b64encode(hashlib.sha1(key + WS_MAGIC).digest()).decode()
        writer.write(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                     b"Sec-WebSocket-Accept: " + accept.encode() + b"\r\n\r\n")
        log("  -> 101", path)
        if sess is None:
            await serve_events(reader, writer)
        else:
            await serve_pty(reader, writer, sess, q)
        log("  ws closed", path)
        writer.close()
        return

    # ── API ──
    # Reads need the token too — the same rule as the product (protocol.md "인증"). A looser
    # stub grows a UI that only works here (the Host check drifted apart that way once — roadmap P2).
    if path.startswith("/api/") and not has_token():
        respond(writer, 403, jbody({"error": "bad token"}))
        await finish()
        return
    if path == "/api/sessions" and method == "GET":
        respond(writer, 200, jbody([s.json() for s in SESSIONS.values()]))
    elif path == "/api/sessions" and method == "POST":
        if not has_token():
            respond(writer, 403, jbody({"error": "bad token"}))
        else:
            b = await body_json()
            cwd = Path(str(b.get("cwd", ""))).expanduser()
            try:
                cwd = cwd.resolve()
            except OSError:
                cwd = None
            cid = b.get("canvas")
            ok_name, name = clean_name(b.get("name"))
            if not cwd or not cwd.is_dir() or not in_roots(cwd):
                respond(writer, 400, jbody({"error": "cwd is outside the roots"}))
            elif cid is not None and canvas_by_id(cid) is None:
                respond(writer, 400, jbody({"error": "no such canvas"}))
            elif not ok_name:
                respond(writer, 400, jbody({"error": "name must be 1-64 characters"}))
            else:
                # with no canvas, the one first in order (protocol.md) — there is never a moment without a canvas
                cid = cid or (CANVASES[0].id if CANVASES else None)
                s = Sess(str(cwd), canvas=cid, name=name,
                         banner=f"dev-stub: fake shell in {cwd} (echo only)\r\n$ ".encode())
                SESSIONS[s.id] = s
                broadcast({"t": "session", "s": s.json()})
                respond(writer, 201, jbody(s.json()))
    elif path.startswith("/api/sessions/") and method == "PATCH":
        # ⑫ name · ⑪ move canvas. **Only keys present in the body change.** No new message — it goes over session
        if not has_token():
            respond(writer, 403, jbody({"error": "bad token"}))
        else:
            b = await body_json()
            sess = SESSIONS.get(path[len("/api/sessions/"):])
            if sess is None:
                respond(writer, 404, jbody({"error": "no such session"}))
            elif not isinstance(b, dict):
                respond(writer, 400, jbody({"error": "body must be an object"}))
            else:
                ok_name, name = clean_name(b.get("name")) if "name" in b else (True, None)
                cid = b.get("canvas") if "canvas" in b else None
                if not ok_name:
                    respond(writer, 400, jbody({"error": "name must be 1-64 characters"}))
                elif "canvas" in b and canvas_by_id(cid) is None:
                    # the session in the path exists, so this is not a 404
                    respond(writer, 400, jbody({"error": "no such canvas"}))
                else:
                    was = (sess.name, sess.canvas)
                    if "name" in b:
                        sess.name = name
                    if "canvas" in b:
                        sess.canvas = cid
                    # **Broadcast only when the value changed** — like the daemon (protocol.md).
                    # A PATCH per keystroke from the name field will not make the broadcasts stampede.
                    # (matched in the 2026-09-08 integration)
                    if (sess.name, sess.canvas) != was:
                        broadcast({"t": "session", "s": sess.json()})
                    respond(writer, 200, jbody(sess.json()))
    elif path.startswith("/api/sessions/") and method == "DELETE":
        if not has_token():
            respond(writer, 403, jbody({"error": "bad token"}))
        else:
            s = SESSIONS.pop(path[len("/api/sessions/"):], None)
            if s is None:
                respond(writer, 404, jbody({"error": "no such session"}))
            else:
                for w in list(s.watchers):
                    try:
                        w.write(Frame.build(b"", 0x8))
                        w.close()
                    except Exception:
                        pass
                if DELAY_GONE[0] > 0:
                    asyncio.get_event_loop().call_later(
                        DELAY_GONE[0], broadcast, {"t": "gone", "id": s.id})
                else:
                    broadcast({"t": "gone", "id": s.id})
                respond(writer, 204)
    elif path == "/api/canvases" and method == "GET":
        respond(writer, 200, jbody([c.json() for c in CANVASES]))
    elif path == "/api/canvases" and method == "POST":
        if not has_token():
            respond(writer, 403, jbody({"error": "bad token"}))
        else:
            b = await body_json()
            ok_name, name = clean_name(b.get("name") if isinstance(b, dict) else None)
            if not ok_name:
                respond(writer, 400, jbody({"error": "name must be 1-64 characters"}))
            else:
                c = Canvas(name)
                CANVASES.append(c)          # **appended at the end** — the order of the existing ones does not change
                renumber()
                broadcast({"t": "canvas", "c": c.json()})
                respond(writer, 201, jbody(c.json()))
    elif path == "/api/canvases/order" and method == "POST":
        # Order is a property of the set, so it comes all at once. Not an exact reordering of the current set → 409.
        if not has_token():
            respond(writer, 403, jbody({"error": "bad token"}))
        else:
            b = await body_json()
            order = b.get("order") if isinstance(b, dict) else None
            if not isinstance(order, list) or any(not isinstance(x, str) for x in order):
                respond(writer, 400, jbody({"error": "order must be a list of ids"}))
            elif sorted(order) != sorted(c.id for c in CANVASES) or len(set(order)) != len(order):
                respond(writer, 409, jbody({"error": "the canvas list changed — try again"}))
            else:
                by_id = {c.id: c for c in CANVASES}
                CANVASES[:] = [by_id[i] for i in order]
                renumber()
                broadcast({"t": "canvases", "cs": [c.json() for c in CANVASES]})
                respond(writer, 200, jbody([c.json() for c in CANVASES]))
    elif path.startswith("/api/canvases/") and method == "PATCH":
        if not has_token():
            respond(writer, 403, jbody({"error": "bad token"}))
        else:
            b = await body_json()
            c = canvas_by_id(path[len("/api/canvases/"):])
            # **Only keys present in the body change.** The old version wiped the name through
            # clean_name(None) even when no key was there — the daemon does not (`if "name" in obj` in
            # palmar/daemon.py). If the stub differs from the daemon, what works here stops working for real.
            # (matched in the 2026-09-08 integration)
            has_name = isinstance(b, dict) and "name" in b
            ok_name, name = clean_name(b.get("name")) if has_name else (True, None)
            if c is None:
                respond(writer, 404, jbody({"error": "no such canvas"}))
            elif not isinstance(b, dict):
                respond(writer, 400, jbody({"error": "body must be an object"}))
            elif not ok_name:
                respond(writer, 400, jbody({"error": "name must be 1-64 characters"}))
            else:
                # **Broadcast only when the value changed** (protocol.md "둘째 브라우저가 무엇으로 따라오는가").
                if has_name and name != c.name:
                    c.name = name
                    broadcast({"t": "canvas", "c": c.json()})
                respond(writer, 200, jbody(c.json()))
    elif path.startswith("/api/canvases/") and method == "DELETE":
        # PROVISIONAL (protocol.md): only empty ones are removed, the last one cannot be. Both 409.
        if not has_token():
            respond(writer, 403, jbody({"error": "bad token"}))
        else:
            c = canvas_by_id(path[len("/api/canvases/"):])
            if c is None:
                respond(writer, 404, jbody({"error": "no such canvas"}))
            elif any(x.canvas == c.id for x in SESSIONS.values()):
                respond(writer, 409, jbody({"error": "move the terminals out of this canvas first"}))
            elif len(CANVASES) <= 1:
                respond(writer, 409, jbody({"error": "the last canvas cannot be removed"}))
            else:
                CANVASES.remove(c)
                renumber()
                # The two frames' order is part of the contract — canvas_gone first, so the receiver clears the tab state
                broadcast({"t": "canvas_gone", "id": c.id})
                broadcast({"t": "canvases", "cs": [x.json() for x in CANVASES]})
                respond(writer, 204)
    elif path == "/api/dirs" and method == "GET":
        d = list_dirs(q.get("path", [None])[0])
        if d is None:
            respond(writer, 400, jbody({"error": "path is outside the roots"}))
        else:
            respond(writer, 200, jbody(d))
    elif path == "/api/dirs" and method == "POST":
        if not has_token():
            respond(writer, 403, jbody({"error": "bad token"}))
        else:
            await body_json()
            respond(writer, 501, jbody({"error": "dev-stub is read-only — folder creation is the daemon's job"}))
    elif path == "/hook/claude" and method == "POST":
        # Hooks always get 200 {}. An unknown pane gets 200 too.
        # **But a wrong token changes no status** — the product does the same (protocol.md "인증").
        # Otherwise anyone could turn someone else's pane `waiting` without a token, and force a broadcast.
        hook = await body_json()
        s = SESSIONS.get(q.get("pane", [""])[0]) if has_token() else None
        ev = hook.get("hook_event_name")
        if s is not None and ev in HOOK_STATUS:
            s.status = HOOK_STATUS[ev]
            s.last_event = ev
            s.agent = None if ev == "SessionEnd" else "claude"
            broadcast({"t": "session", "s": s.json()})
        respond(writer, 200, b"{}")
    elif method == "GET":
        # ── static files ──
        name = path.lstrip("/") or "index.html"
        f = (WEB / name).resolve()
        if not f.is_relative_to(WEB) or not f.is_file() or f.name == "dev-stub.py":
            respond(writer, 404, jbody({"error": "not found"}))
        else:
            data = f.read_bytes()
            # Compared in lowercase — macOS does not tell case apart in names, so `/INDEX.HTML` fetches
            # index.html, and a raw comparison lets `.HTML` skip the gate (same reason as the product's
            # serve_static).
            suffix = f.suffix.lower()
            if suffix == ".html":
                # The only request carrying the token, so the key is asked only here (same spot as the
                # product's serve_static). **Compared as bytes** — `hmac.compare_digest` raises TypeError on
                # a non-ASCII str, and with nobody catching it here the connection hung with no reply
                # (measured: `?k=한글` gave no answer).
                got = q.get("k", [""])[0].encode("utf-8", "surrogatepass")
                if not hmac.compare_digest(got, KEY.encode()):
                    respond(writer, 403, jbody({"error": "이 주소에는 ?k= 가 필요하다 — 스텁이 찍은 주소로 열어라"}))
                    await finish()
                    return
                data = data.replace(b"</head>", f'<script>window.PALMAR_TOKEN="{TOKEN}"</script></head>'.encode(), 1)
            ctype = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                     ".css": "text/css; charset=utf-8"}.get(suffix, "application/octet-stream")
            respond(writer, 200, data, ctype)
    else:
        respond(writer, 404, jbody({"error": "not found"}))
    await finish()


async def serve_events(reader, writer):
    EVENT_CLIENTS.add(writer)
    # Within one frame, every session.canvas is present in this canvases list (protocol.md)
    writer.write(Frame.text({"t": "hello", "v": PROTOCOL,
                             "canvases": [c.json() for c in CANVASES],
                             "sessions": [s.json() for s in SESSIONS.values()]}))
    try:
        while True:
            opcode, payload = await read_frame(reader)
            if opcode == 0x8:
                break
            if opcode == 0x1:
                try:
                    msg = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if msg.get("t") == "seen":
                    s = SESSIONS.get(msg.get("id"))
                    if s is not None and s.status == "done":
                        s.status = "idle"
                        broadcast({"t": "session", "s": s.json()})
    except (asyncio.IncompleteReadError, ConnectionResetError):
        pass
    finally:
        EVENT_CLIENTS.discard(writer)


async def serve_pty(reader, writer, sess, q):
    try:
        frm = max(0, int(q.get("from", ["0"])[0]))
    except ValueError:
        frm = 0
    replay = bytes(sess.buf[frm:]) if frm < len(sess.buf) else b""
    # protocol.md: offset is the absolute offset after the replay, replayed is how many bytes were replayed
    writer.write(Frame.text({"t": "hello", "offset": len(sess.buf), "alt": sess.alt, "replayed": len(replay)}))
    if replay:
        writer.write(Frame.build(replay))
    sess.watchers.add(writer)
    acked = 0
    try:
        while True:
            opcode, payload = await read_frame(reader)
            if opcode == 0x8:
                break
            if opcode == 0x2:
                # echo: imitates a PTY that is not there. CR is a new prompt, DEL erases, ^C cancels
                out = bytearray()
                for b in payload:
                    if b == 13:
                        out += b"\r\n$ "
                    elif b == 127:
                        out += b"\b \b"
                    elif b == 3:
                        out += b"^C\r\n$ "
                    else:
                        out.append(b)
                sess.emit(bytes(out))
            elif opcode == 0x1:
                try:
                    msg = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if msg.get("t") == "resize":
                    sess.cols, sess.rows = int(msg.get("cols", sess.cols)), int(msg.get("rows", sess.rows))
                    broadcast({"t": "session", "s": sess.json()})
                elif msg.get("t") == "ack":
                    acked += int(msg.get("n", 0))
    except (asyncio.IncompleteReadError, ConnectionResetError):
        pass
    finally:
        sess.watchers.discard(writer)


async def main():
    ap = argparse.ArgumentParser(description="palmar dev stub — NOT the product")
    ap.add_argument("--port", type=int, default=8801)
    ap.add_argument("--debug", action="store_true", help="log every request to stderr")
    ap.add_argument("--delay-gone", type=float, default=0.0, metavar="SEC",
                    help="delay the /events `gone` broadcast after DELETE (to watch what the browser does meanwhile)")
    args = ap.parse_args()
    PORT[0] = args.port
    DEBUG[0] = args.debug
    DELAY_GONE[0] = max(0.0, args.delay_gone)
    seed()
    server = await asyncio.start_server(handle, "127.0.0.1", args.port)
    print(f"dev-stub (not the product)  pid {os.getpid()}  token {TOKEN}", flush=True)
    print(f"http://127.0.0.1:{args.port}/?k={KEY}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
