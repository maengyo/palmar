#!/usr/bin/env python3
"""스파이크 D — PTY → 웹소켓 → xterm.js 파이프라인 (파이썬 판)

**표준 라이브러리만 쓴다.** 웹소켓 서버도 손으로 짠다 — 그게 이 스파이크가 답하려는 것 중
하나이기 때문이다(`docs/decisions.md` ②).

단일 스레드 asyncio 다. `pty.fork` 는 스레드가 있으면 경고를 내고 교착 위험이 있다(조사).

프로토콜 (pane 마다 웹소켓 하나):
    GET /pty?cols=&rows=&cmd=       업그레이드하면 셸이 뜬다
    서버 → 클라이언트: 바이너리 = PTY 바이트
    클라이언트 → 서버: 바이너리 = 키 입력
                       텍스트 {"t":"resize","cols":N,"rows":N}
                       텍스트 {"t":"ack","n":바이트수}      ← 흐름 제어

흐름 제어: 미확인 바이트가 HIGH 를 넘으면 PTY 읽기를 멈추고, LOW 아래로 내려오면 다시 읽는다.
숫자는 ttyd·VS Code 가 쓰는 값에서 가져왔다(조사).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import fcntl
import hashlib
import json
import os
import pty
import signal
import struct
import sys
import termios
from pathlib import Path
from urllib.parse import parse_qs, urlparse

WS_MAGIC = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

#: 미확인 바이트가 이만큼 쌓이면 PTY 읽기를 멈춘다. ttyd 는 100KB x 10, VS Code 는 100KB.
HIGH_WATER = 100_000
#: 여기까지 내려오면 다시 읽는다. VS Code 는 5KB.
LOW_WATER = 10_000
#: 한 번 깨어났을 때 삼킬 최대 바이트. 없으면 끝없이 뱉는 프로그램이 이벤트 루프를 잡는다(polycanv).
PUMP_BUDGET = 256 * 1024
#: PTY 바이트를 이만큼 모았다 보낸다. macOS PTY 는 1KB 씩 읽히므로 안 모으면 프레임이 폭주한다(조사).
COALESCE_MS = 5

WEB = Path(__file__).resolve().parent.parent / "web"


def set_winsize(fd: int, rows: int, cols: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


class Frame:
    """RFC 6455 프레이밍. 서버는 마스킹하지 않고, 클라이언트 것은 언마스킹한다."""

    @staticmethod
    def build(payload: bytes, opcode: int = 0x2) -> bytes:
        n = len(payload)
        if n < 126:
            head = struct.pack("!BB", 0x80 | opcode, n)
        elif n < 65536:
            head = struct.pack("!BBH", 0x80 | opcode, 126, n)
        else:
            head = struct.pack("!BBQ", 0x80 | opcode, 127, n)
        return head + payload


class Pane:
    """PTY 하나와 그것을 보는 웹소켓 하나."""

    def __init__(self, reader, writer, cmd: list[str], cols: int, rows: int, cwd: str):
        self.reader, self.writer = reader, writer
        self.unacked = 0
        self.reading = False
        self.pending = bytearray()
        self.flush_handle = None
        self.closed = False

        self.pid, self.master = pty.fork()
        if self.pid == 0:  # 자식 — 여기서 돌아오지 않는다
            os.environ["TERM"] = "xterm-256color"
            os.environ["PALMER_PANE"] = "spike"
            try:
                os.chdir(cwd)
            except OSError:
                pass
            os.execvp(cmd[0], cmd)
        set_winsize(self.master, rows, cols)
        os.set_blocking(self.master, False)

    # ── PTY → 브라우저 ────────────────────────────────
    def start_reading(self) -> None:
        if self.reading or self.closed:
            return
        asyncio.get_running_loop().add_reader(self.master, self._on_readable)
        self.reading = True

    def stop_reading(self) -> None:
        if not self.reading:
            return
        asyncio.get_running_loop().remove_reader(self.master)
        self.reading = False

    def _on_readable(self) -> None:
        got = 0
        while got < PUMP_BUDGET:
            try:
                chunk = os.read(self.master, 65536)
            except BlockingIOError:
                break
            except OSError:
                # 리눅스는 자식이 죽으면 EIO, macOS 는 빈 바이트다(조사).
                self.close()
                return
            if not chunk:
                self.close()
                return
            self.pending += chunk
            got += len(chunk)
        if self.pending and self.flush_handle is None:
            loop = asyncio.get_running_loop()
            self.flush_handle = loop.call_later(COALESCE_MS / 1000, self._flush)
        if self.unacked >= HIGH_WATER:
            self.stop_reading()

    def _flush(self) -> None:
        self.flush_handle = None
        if not self.pending or self.closed:
            return
        data = bytes(self.pending)
        self.pending.clear()
        self.unacked += len(data)
        try:
            self.writer.write(Frame.build(data))
        except Exception:
            self.close()

    def ack(self, n: int) -> None:
        self.unacked = max(0, self.unacked - n)
        if not self.reading and self.unacked <= LOW_WATER:
            self.start_reading()

    # ── 브라우저 → PTY ────────────────────────────────
    def send_input(self, data: bytes) -> None:
        if not self.closed:
            try:
                os.write(self.master, data)
            except OSError:
                self.close()

    def resize(self, cols: int, rows: int) -> None:
        if not self.closed:
            try:
                set_winsize(self.master, rows, cols)
            except OSError:
                pass

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.stop_reading()
        try:
            os.close(self.master)
        except OSError:
            pass
        try:
            os.kill(self.pid, signal.SIGHUP)
        except ProcessLookupError:
            pass
        try:
            self.writer.close()
        except Exception:
            pass


async def read_frame(reader) -> tuple[int, bytes] | None:
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


async def handle(reader, writer):
    try:
        request = await reader.readuntil(b"\r\n\r\n")
    except Exception:
        writer.close()
        return
    lines = request.decode("latin-1").split("\r\n")
    method, path, _ = lines[0].split(" ", 2)
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()

    url = urlparse(path)
    if url.path == "/pty" and headers.get("upgrade", "").lower() == "websocket":
        key = headers["sec-websocket-key"].encode()
        accept = base64.b64encode(hashlib.sha1(key + WS_MAGIC).digest()).decode()
        writer.write(
            b"HTTP/1.1 101 Switching Protocols\r\n"
            b"Upgrade: websocket\r\nConnection: Upgrade\r\n"
            b"Sec-WebSocket-Accept: " + accept.encode() + b"\r\n\r\n"
        )
        q = parse_qs(url.query)
        cols = int(q.get("cols", ["80"])[0])
        rows = int(q.get("rows", ["24"])[0])
        cmd = q.get("cmd", [os.environ.get("SHELL", "/bin/sh")])[0].split(" ")
        pane = Pane(reader, writer, cmd, cols, rows, q.get("cwd", [os.path.expanduser("~")])[0])
        pane.start_reading()
        try:
            while True:
                frame = await read_frame(reader)
                if frame is None:
                    break
                opcode, payload = frame
                if opcode == 0x8:  # close
                    break
                if opcode == 0x2:  # binary = 키 입력
                    pane.send_input(payload)
                elif opcode == 0x1:  # text = 제어
                    msg = json.loads(payload)
                    if msg["t"] == "resize":
                        pane.resize(msg["cols"], msg["rows"])
                    elif msg["t"] == "ack":
                        pane.ack(msg["n"])
        except (asyncio.IncompleteReadError, ConnectionResetError, json.JSONDecodeError):
            pass
        finally:
            pane.close()
        return

    if url.path == "/report" and method == "POST":
        n = int(headers.get("content-length", "0"))
        body = await reader.readexactly(n) if n else b"{}"
        with open(os.environ.get("PALMER_REPORT", "/tmp/palmer-report.jsonl"), "a") as fh:
            fh.write(body.decode() + "\n")
        writer.write(b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\n\r\n")
        await writer.drain()
        writer.close()
        return

    # 정적 파일
    name = url.path.lstrip("/") or "index.html"
    f = (WEB / name).resolve()
    if not str(f).startswith(str(WEB)) or not f.is_file():
        writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
    else:
        body = f.read_bytes()
        ctype = {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}.get(
            f.suffix, "application/octet-stream"
        )
        writer.write(
            f"HTTP/1.1 200 OK\r\nContent-Type: {ctype}; charset=utf-8\r\n"
            f"Content-Length: {len(body)}\r\nCache-Control: no-store\r\n\r\n".encode()
            + body
        )
    await writer.drain()
    writer.close()


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8801)
    args = ap.parse_args()
    # 127.0.0.1 밖으로 열지 않는다 — host 를 바꾸는 옵션을 두지 않는 것이 규칙이다.
    server = await asyncio.start_server(handle, "127.0.0.1", args.port)
    print(f"python  http://127.0.0.1:{args.port}  pid {os.getpid()}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
