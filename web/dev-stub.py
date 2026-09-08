#!/usr/bin/env python3
"""dev-stub — 개발용 가짜 데몬. **제품이 아니다.** 제품은 server/palmerd.py 다.

브라우저 쪽(web/)을 데몬 없이 띄워 보려고 docs/protocol.md 의 겉모양만 흉내 낸다:
    GET  /, /app.js …            web/ 정적 파일. index.html 에 window.PALMER_TOKEN 을 심는다
    WS   /events?token=          hello 에 가짜 세션 둘. seen 을 받으면 done → idle
    WS   /pty/<id>?token=…       hello 프레임 + 링버퍼 재생 + **키 입력을 그대로 되돌려 준다(echo)**. PTY 는 없다
    GET  /api/sessions           목록 — **캔버스로 거르지 않는다**(왼쪽 목록이 전부를 본다)
    POST /api/sessions?token=    가짜 세션을 하나 더 만든다(셸은 안 뜬다). cwd·canvas·name 을 받는다
    PATCH /api/sessions/<id>     이름 바꾸기·캔버스 옮기기 (⑫ ⑪)
    DELETE /api/sessions/<id>    지운다 (--delay-gone 으로 `gone` 방송만 늦출 수 있다 — 아래 DELAY_GONE)
    GET  /api/canvases           order 순 목록                      (⑪)
    POST /api/canvases?token=    끝에 하나 붙인다
    POST /api/canvases/order     지금 있는 전부를 새 순서로 (409 로 어긋남을 알린다)
    PATCH /api/canvases/<id>     이름 바꾸기
    DELETE /api/canvases/<id>    빈 것만·마지막 하나는 못 지움 (둘 다 409) — protocol.md 의 PROVISIONAL
    GET  /api/dirs[?path=]       **진짜 파일시스템을 읽기 전용으로** 본다 — 폴더만, 점 제외, .git/HEAD 의 브랜치
    POST /api/dirs               501 — 이 스텁은 아무것도 만들지 않는다
    POST /hook/claude?pane=      훅 JSON 의 hook_event_name 을 protocol.md 표대로 status 에 반영한다 (curl 로 상태 전환 시험용)

표준 라이브러리만, 파이썬 3.9. 웹소켓 프레이밍은 스파이크 D(docs/spikes/2026-09-07/pipeline/py/server.py)에서 가져왔다.
127.0.0.1 에만 묶고 Origin·Host·토큰을 보고, 모든 응답에 X-Frame-Options·frame-ancestors 를 붙인다
(protocol.md "인증" 그대로) — 스텁이라도 셸 흉내를 아무 사이트에 열어 두지는 않는다.
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
WEB = Path(__file__).resolve().parent
TOKEN = secrets.token_urlsafe(32)
PORT = [8801]
HOME = Path.home().resolve()
DEBUG = [False]
# --delay-gone: DELETE 에 204 를 준 뒤 `gone` 방송을 이만큼 늦춘다(초). 0 이면 지금처럼 바로.
# 브라우저가 **DELETE 를 낸 뒤 gone 이 올 때까지 줄과 타일을 그대로 두는지** 를 눈으로 보려고 둔 손잡이다
# (protocol.md 는 "지우는 것은 gone" 이라고만 하지 그 사이를 안 적는다 — 그 사이가 없으면 못 본다).
DELAY_GONE = [0.0]


def log(*a):
    if DEBUG[0]:
        print(time.strftime("%H:%M:%S"), *a, file=sys.stderr, flush=True)

# 훅 이벤트 → status (protocol.md "상태" 표)
HOOK_STATUS = {
    "SessionStart": "idle", "UserPromptSubmit": "working", "PermissionRequest": "waiting",
    "Stop": "done", "SessionEnd": "unknown",
}


# ── 웹소켓 프레이밍 (스파이크 D 그대로) ─────────────────────
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


# ── 캔버스 (⑪) ─────────────────────────────────────────────
class Canvas:
    def __init__(self, name=None):
        self.id = secrets.token_urlsafe(16)      # 세션 id 와 같은 모양 (protocol.md "캔버스")
        self.name = name
        self.order = 0

    def json(self):
        return {"id": self.id, "name": self.name, "order": self.order}


CANVASES = []   # order 순. 빈틈없이 0부터 다시 매긴다


def renumber():
    for i, c in enumerate(CANVASES):
        c.order = i


def canvas_by_id(cid):
    for c in CANVASES:
        if c.id == cid:
            return c
    return None


def clean_name(v):
    """protocol.md "이름 규칙": 문자열이면 앞뒤 공백을 떼고 1–64자, 제어문자 금지.

    돌려주는 것은 (ok, name). null·빈 문자열·공백뿐인 것은 모두 이름 없음(None)으로 같게 다룬다.
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


# ── 가짜 세션 ───────────────────────────────────────────────
class Sess:
    def __init__(self, cwd, status="unknown", agent=None, last_event=None, banner=b"",
                 canvas=None, name=None):
        self.id = secrets.token_urlsafe(16)          # 22자, 추측 불가 (protocol.md Session.id)
        self.cwd = cwd
        self.canvas = canvas                         # ⑪ null 이 아니다 — 세션은 늘 어딘가에 있다
        self.name = name                             # ⑫ 사람이 준 이름. None 이면 브라우저가 이름표를 만든다
        self.cols, self.rows = 80, 24
        self.status, self.agent, self.alt = status, agent, False
        self.created = time.time()
        self.last_event = last_event
        self.buf = bytearray(banner)                # 링버퍼 흉내 — 여기서는 안 자른다
        self.watchers = set()                       # /pty 로 붙은 writer 들

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
    """캔버스 셋에 세션 다섯을 흩는다.

    브라우저는 order 가 맨 앞인 캔버스를 처음 보므로, **기다리는 것 하나는 둘째 캔버스**에 둔다 —
    그래야 "다른 캔버스의 탭에 점이 켜지고, 왼쪽 목록에는 배지가 붙은 채 맨 위에 온다" 가 눈에 보인다.
    """
    c1, c2, c3 = Canvas("api"), Canvas(), Canvas("scratch")   # c2 는 이름 없음 → 브라우저가 이름표를 만든다
    CANVASES.extend([c1, c2, c3])
    renumber()

    proj = HOME / "ddul" / "python" / "palmer"
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
             name="auth refactor",      # ⑫ 이름이 붙은 것 하나 — 이름표가 경로를 이긴다
             banner=b"dev-stub: fake pane (echo only)\r\n$ claude\r\n")
    c = Sess(str(HOME), canvas=c1.id, banner=b"dev-stub: fake shell (echo only)\r\n$ ")
    d = Sess(a_cwd, status="done", agent="claude", last_event="Stop", canvas=c3.id,
             banner=b"dev-stub: fake pane (echo only)\r\n$ ")
    e = Sess(str(HOME), canvas=c3.id, name="notes", banner=b"dev-stub: fake shell (echo only)\r\n$ ")
    for x in (a, b, c, d, e):
        SESSIONS[x.id] = x


# ── 디렉터리 (읽기 전용) ─────────────────────────────────────
def owned_by_me(p) -> bool:
    """resolve() 된 경로의 소유자가 지금 uid 인가. palmerd.owned_by_me 와 같은 술어다 (#31)."""
    try:
        return os.stat(str(p)).st_uid == os.getuid()
    except OSError:
        return False


def roots():
    """사용자 홈 + **내가 가진** /Users/* /home/*.

    이 스텁은 `/api/dirs` 에서 진짜 파일시스템을 읽으므로 뿌리 규칙도 진짜와 같아야 한다
    (protocol.md "뿌리(roots)": 데몬 자리에 서는 것은 같은 규칙을 지킨다). uid 검사가 빠져 있으면
    WSL 에서 `aa` 로 스텁을 띄웠을 때 `/home/bb` 가 디렉터리 레일에 그대로 뜬다 — #31 ② 가 데몬에서
    고친 바로 그 자리다. 소유자는 palmerd 와 같이 **푼 경로(resolve)** 에서 잰다."""
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
    """<dir>/.git/HEAD 한 줄만 읽는다. .git 이 파일이면(worktree) gitdir: 을 따라간다. 못 읽으면 None."""
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
        # 뿌리 목록: path 는 비우고 name 에 절대 경로를 준다 (protocol.md 가 이 부분을 못 박지 않았다)
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


# ── HTTP 도우미 ─────────────────────────────────────────────
def respond(writer, status, body=b"", ctype="application/json; charset=utf-8", extra=""):
    reason = {200: "OK", 201: "Created", 204: "No Content", 400: "Bad Request", 403: "Forbidden",
              404: "Not Found", 405: "Method Not Allowed", 409: "Conflict",
              501: "Not Implemented"}.get(status, "OK")
    # protocol.md "인증": 모든 HTTP 응답에 붙는다. 제품(server/palmerd.py http())과 같은 두 줄이다 —
    # 스텁도 index.html 에 토큰을 심으므로 iframe 으로 감싸이면 잃을 것이 제품과 같다(#10).
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
        # protocol.md "인증": Host 도 같은 둘만. DNS 리바인딩(공격자 도메인 → 127.0.0.1)으로
        # index.html 의 토큰을 읽어 가는 길을 막는다. 제품 palmerd.allowed_host() 와 같은 규칙이다 —
        # 스텁도 같은 자리에 토큰을 심으므로 여기만 열려 있으면 스텁이 그 길이 된다(실측 2026-09-08:
        # Host: evil.example 로 GET / 가 200 이었고 몸에 PALMER_TOKEN 이 그대로 있었다).
        h = headers.get("host")
        return h is None or h in (f"127.0.0.1:{PORT[0]}", f"localhost:{PORT[0]}")

    def has_token():
        got = q.get("token", [""])[0]
        ok = hmac.compare_digest(got, TOKEN)
        if not ok:
            log("  token mismatch: got", len(got), "chars, query keys", sorted(q))
        return ok

    async def body_json():
        n = int(headers.get("content-length", "0") or 0)
        raw = await reader.readexactly(n) if n else b"{}"
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

    # ── 웹소켓 ──
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
                # canvas 가 없으면 order 가 가장 앞인 캔버스 (protocol.md) — 캔버스가 없는 순간은 없다
                cid = cid or (CANVASES[0].id if CANVASES else None)
                s = Sess(str(cwd), canvas=cid, name=name,
                         banner=f"dev-stub: fake shell in {cwd} (echo only)\r\n$ ".encode())
                SESSIONS[s.id] = s
                broadcast({"t": "session", "s": s.json()})
                respond(writer, 201, jbody(s.json()))
    elif path.startswith("/api/sessions/") and method == "PATCH":
        # ⑫ 이름 · ⑪ 캔버스 옮기기. **몸에 있는 키만 바꾼다.** 새 메시지는 없다 — session 하나로 간다
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
                    # 경로의 세션은 있으니 404 가 아니다
                    respond(writer, 400, jbody({"error": "no such canvas"}))
                else:
                    was = (sess.name, sess.canvas)
                    if "name" in b:
                        sess.name = name
                    if "canvas" in b:
                        sess.canvas = cid
                    # **값이 달라졌을 때만 방송한다** — 데몬과 같게(protocol.md). 이름 입력칸이 글자마다
                    # PATCH 를 날려도 방송이 폭주하지 않는다. (2026-09-08 통합에서 맞춤)
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
                CANVASES.append(c)          # **끝에 붙는다** — 있던 것의 order 는 안 바뀐다
                renumber()
                broadcast({"t": "canvas", "c": c.json()})
                respond(writer, 201, jbody(c.json()))
    elif path == "/api/canvases/order" and method == "POST":
        # 순서는 집합의 성질이라 한 번에 받는다. 지금 집합의 재배열이 정확히 아니면 409.
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
            # **몸에 있는 키만 바꾼다.** 옛 판은 키가 없어도 clean_name(None) 을 거쳐 이름을 지웠다 —
            # 데몬은 안 그런다(server/palmerd.py 의 `if "name" in obj`). 스텁이 데몬과 다르면 여기서
            # 되던 것이 진짜에서 안 된다. (2026-09-08 통합에서 맞춤)
            has_name = isinstance(b, dict) and "name" in b
            ok_name, name = clean_name(b.get("name")) if has_name else (True, None)
            if c is None:
                respond(writer, 404, jbody({"error": "no such canvas"}))
            elif not isinstance(b, dict):
                respond(writer, 400, jbody({"error": "body must be an object"}))
            elif not ok_name:
                respond(writer, 400, jbody({"error": "name must be 1-64 characters"}))
            else:
                # **값이 달라졌을 때만 방송한다** (protocol.md "둘째 브라우저가 무엇으로 따라오는가").
                if has_name and name != c.name:
                    c.name = name
                    broadcast({"t": "canvas", "c": c.json()})
                respond(writer, 200, jbody(c.json()))
    elif path.startswith("/api/canvases/") and method == "DELETE":
        # PROVISIONAL (protocol.md): 빈 것만 지운다, 마지막 하나는 못 지운다. 둘 다 409.
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
                # 두 프레임의 순서는 계약이다 — canvas_gone 을 먼저 보내야 받는 쪽이 탭 상태를 지운다
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
        # 훅은 항상 200 {}. 모르는 pane 도 200.
        hook = await body_json()
        s = SESSIONS.get(q.get("pane", [""])[0])
        ev = hook.get("hook_event_name")
        if s is not None and ev in HOOK_STATUS:
            s.status = HOOK_STATUS[ev]
            s.last_event = ev
            s.agent = None if ev == "SessionEnd" else "claude"
            broadcast({"t": "session", "s": s.json()})
        respond(writer, 200, b"{}")
    elif method == "GET":
        # ── 정적 파일 ──
        name = path.lstrip("/") or "index.html"
        f = (WEB / name).resolve()
        if not f.is_relative_to(WEB) or not f.is_file() or f.name == "dev-stub.py":
            respond(writer, 404, jbody({"error": "not found"}))
        else:
            data = f.read_bytes()
            if f.suffix == ".html":
                data = data.replace(b"</head>", f'<script>window.PALMER_TOKEN="{TOKEN}"</script></head>'.encode(), 1)
            ctype = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                     ".css": "text/css; charset=utf-8"}.get(f.suffix, "application/octet-stream")
            respond(writer, 200, data, ctype)
    else:
        respond(writer, 404, jbody({"error": "not found"}))
    await finish()


async def serve_events(reader, writer):
    EVENT_CLIENTS.add(writer)
    # 한 프레임 안에서 모든 session.canvas 가 이 canvases 안에 있다 (protocol.md)
    writer.write(Frame.text({"t": "hello",
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
    # protocol.md: offset 은 재생 뒤의 절대 오프셋, replayed 는 재생한 바이트 수
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
                # 에코: 없는 PTY 를 흉내 낸다. CR 은 새 프롬프트, DEL 은 지우기, ^C 는 취소
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
    ap = argparse.ArgumentParser(description="palmer dev stub — NOT the product")
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
    print(f"http://127.0.0.1:{args.port}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
