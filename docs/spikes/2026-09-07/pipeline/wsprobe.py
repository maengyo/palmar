#!/usr/bin/env python3
"""브라우저 없이 서버 쪽만 재는 도구.

xterm.js 를 흉내 내는 클라이언트 N 개를 붙여 최대 출력을 흘리고,
서버 프로세스의 CPU·RSS 와 클라이언트가 받은 처리량을 잰다.
브라우저 쪽 프레임 시간은 이걸로 못 잰다 — 그건 사람이 페이지를 열고 봐야 한다.
"""

import argparse, base64, json, os, socket, struct, subprocess, sys, threading, time

MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def cputime(pid: int) -> float:
    out = subprocess.check_output(["ps", "-p", str(pid), "-o", "cputime="]).decode().strip()
    parts = [float(x) for x in out.split(":")]
    return sum(p * 60 ** i for i, p in enumerate(reversed(parts)))


def rss_mb(pid: int) -> float:
    return int(subprocess.check_output(["ps", "-p", str(pid), "-o", "rss="]).decode()) / 1024


class Client(threading.Thread):
    """웹소켓 pane 하나. 받은 바이트를 세고 ACK 를 돌려준다(흐름 제어 포함)."""

    def __init__(self, port: int, cols=94, rows=26, ack=True):
        super().__init__(daemon=True)
        self.port, self.cols, self.rows, self.do_ack = port, cols, rows, ack
        self.bytes = 0
        self.frames = 0
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.first_byte_at = None

    def connect(self):
        self.s = socket.create_connection(("127.0.0.1", self.port))
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall(
            f"GET /pty?cols={self.cols}&rows={self.rows}&cmd=/bin/sh HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{self.port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode()
        )
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.s.recv(4096)
        assert b"101" in buf.split(b"\r\n")[0], buf[:80]
        self.rest = buf.split(b"\r\n\r\n", 1)[1]

    def send(self, payload: bytes, opcode: int):
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        n = len(payload)
        if n < 126:
            head = struct.pack("!BB", 0x80 | opcode, 0x80 | n)
        elif n < 65536:
            head = struct.pack("!BBH", 0x80 | opcode, 0x80 | 126, n)
        else:
            head = struct.pack("!BBQ", 0x80 | opcode, 0x80 | 127, n)
        self.s.sendall(head + mask + masked)

    def write(self, text: str):
        self.send(text.encode(), 0x2)

    def _recv(self, n: int) -> bytes:
        while len(self.rest) < n:
            chunk = self.s.recv(1 << 20)
            if not chunk:
                raise ConnectionError
            self.rest += chunk
        out, self.rest = self.rest[:n], self.rest[n:]
        return out

    def run(self):
        try:
            self.connect()
            self.ready.set()
            while not self.stop.is_set():
                head = self._recv(2)
                n = head[1] & 0x7F
                if n == 126:
                    n = struct.unpack("!H", self._recv(2))[0]
                elif n == 127:
                    n = struct.unpack("!Q", self._recv(8))[0]
                payload = self._recv(n) if n else b""
                if (head[0] & 0x0F) == 0x8:
                    break
                self.bytes += len(payload)
                self.frames += 1
                if self.first_byte_at is None and payload:
                    self.first_byte_at = time.time()
                if self.do_ack and payload:
                    self.send(json.dumps({"t": "ack", "n": len(payload)}).encode(), 0x1)
        except Exception:
            pass


def run(port: int, pid: int, npanes: int, flood: int, seconds: float, label: str, ack=True):
    cs = [Client(port, ack=ack) for _ in range(npanes)]
    for c in cs:
        c.start()
    for c in cs:
        c.ready.wait(5)
    time.sleep(0.8)

    for c in cs[:flood]:
        c.write('i=0; while :; do i=$((i+1)); echo "flood $i 0123456789abcdef 0123456789abcdef"; done\n')

    time.sleep(0.3)
    base_b = sum(c.bytes for c in cs)
    c0, r0, t0 = cputime(pid), rss_mb(pid), time.time()
    time.sleep(seconds)
    el = time.time() - t0
    cpu = (cputime(pid) - c0) / el * 100
    rss = rss_mb(pid)
    got = sum(c.bytes for c in cs) - base_b
    fr = sum(c.frames for c in cs)

    for c in cs:
        c.write("\x03")
    time.sleep(0.3)
    for c in cs:
        c.stop.set()
        try:
            c.s.close()
        except Exception:
            pass

    print(f"  {label:34s} CPU {cpu:6.1f}%  RSS {rss:6.1f}MB  "
          f"받은 {got/1048576:6.2f}MB ({got/el/1048576:5.2f}MB/s)  프레임 {fr:6d} ({fr/el:6.1f}/s)")
    return dict(cpu=cpu, rss=rss, mb=got / 1048576, fps=fr / el)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--pid", type=int, required=True)
    ap.add_argument("--name", default="server")
    a = ap.parse_args()
    print(f"\n=== {a.name} (port {a.port}, pid {a.pid}) ===")
    print(f"  {'유휴 시작':34s} RSS {rss_mb(a.pid):6.1f}MB")
    run(a.port, a.pid, 1, 0, 3, "pane 1 · 유휴")
    run(a.port, a.pid, 8, 0, 3, "pane 8 · 유휴")
    run(a.port, a.pid, 1, 1, 4, "pane 1 · 최대 출력")
    run(a.port, a.pid, 8, 1, 4, "pane 8 · 하나만 최대 출력")
    run(a.port, a.pid, 8, 8, 4, "pane 8 · 전부 최대 출력")
    run(a.port, a.pid, 8, 8, 4, "pane 8 · 전부 · ACK 없음(흐름제어 끔)", ack=False)
    run(a.port, a.pid, 16, 1, 4, "pane 16 · 하나만 최대 출력")
