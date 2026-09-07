#!/usr/bin/env python3
"""스파이크 G — 데몬 핸드오프를 실제로 만들어 본다 (#19)

스파이크 A 는 fd 하나가 넘어가는 것만 봤다. 여기서는 **클라이언트가 붙어 있는 채로 데몬을 통째로
교체**한다. 재는 것은 셋이다.

  1. **바이트를 잃는가.** pane 이 `SEQ 1`, `SEQ 2` … 를 찍는다. 클라이언트가 구멍을 센다.
  2. **얼마나 끊기는가.** 마지막 바이트 → 교체 → 첫 바이트까지의 시간.
  3. **코드가 얼마나 드는가.** 넘기는 것은 fd + 링버퍼 + pane 상태다.

`⑦` 의 (a) 를 고를 수 있는지가 이 셋에 달렸다. 스파이크 F 에서 **링버퍼는 셸 pane 때문에 필요하고
에이전트 때문이 아니라는 것**이 나왔으므로, 여기서 넘기는 링버퍼는 셸 기준이다.

역할:
    handoff.py serve  <gen>        데몬. gen 은 세대 번호(1 → 2)
    handoff.py client              붙어서 순번을 세는 쪽 = 브라우저
    handoff.py run                 전부 띄우고 교체시킨 뒤 결과를 낸다

프로토콜(둘 다 줄 단위, 편의를 위해 웹소켓 대신 TCP 다 — 웹소켓은 스파이크 D 가 이미 했다):
    클라이언트 → 데몬 : "ATTACH <pane> <from>\\n"   from 바이트째부터 달라
    데몬 → 클라이언트 : "OFFSET <n>\\n" 뒤로는 원시 바이트
"""

from __future__ import annotations

import argparse
import array
import fcntl
import json
import os
import pty
import re
import select
import signal
import socket
import struct
import subprocess
import sys
import termios
import time

HOST, PORT = "127.0.0.1", 8811
HANDOFF_SOCK = "/tmp/palmer-handoff.sock"
RING = 1 << 20          # pane 당 1MB. 셸 pane 기준이다(스파이크 F).
COLS, ROWS = 100, 30
SELF = os.path.abspath(__file__)

# pane 이 돌릴 것 — 순번을 찍어야 구멍을 셀 수 있다.
# --fast 면 sleep 없이 최대 속도로 찍는다 — 커널 PTY 버퍼가 꽉 찬 채로 교체해 보려는 것.
SLOW = 'i=0; while :; do i=$((i+1)); echo "SEQ $i"; sleep 0.005; done'
FAST = 'i=0; while :; do i=$((i+1)); echo "SEQ $i 0123456789abcdef0123456789abcdef"; done'
PANE_CMD = SLOW


def set_winsize(fd: int, rows: int, cols: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def log(gen, *a):
    print(f"[daemon{gen}]", *a, file=sys.stderr, flush=True)


class Pane:
    """PTY 하나 + 링버퍼. 링버퍼의 절대 오프셋을 들고 있어야 재생 지점을 찾는다."""

    def __init__(self, fd: int, pid: int, produced: int = 0, ring: bytes = b""):
        self.fd, self.pid = fd, pid
        self.produced = produced      # 지금까지 이 pane 이 낸 총 바이트 수 (절대 오프셋)
        self.ring = bytearray(ring)
        os.set_blocking(fd, False)

    @classmethod
    def spawn(cls) -> "Pane":
        pid, fd = pty.fork()
        if pid == 0:
            os.execvpe("/bin/sh", ["/bin/sh", "-c", PANE_CMD], {"TERM": "dumb", "PATH": "/bin:/usr/bin"})
        set_winsize(fd, ROWS, COLS)
        return cls(fd, pid)

    def read(self) -> bytes:
        try:
            b = os.read(self.fd, 65536)
        except (BlockingIOError, OSError):
            return b""
        self.produced += len(b)
        self.ring += b
        if len(self.ring) > RING:
            del self.ring[: len(self.ring) - RING]
        return b

    def since(self, offset: int) -> bytes:
        """offset 바이트째부터의 데이터. 링버퍼에서 밀려났으면 있는 데까지."""
        have_from = self.produced - len(self.ring)
        start = max(0, offset - have_from)
        return bytes(self.ring[start:])

    def state(self) -> dict:
        return {"pid": self.pid, "produced": self.produced, "ring": len(self.ring)}


def serve(gen: int, inherited: tuple | None = None) -> None:
    """데몬. inherited 가 있으면 (Pane, 재개할 클라이언트 오프셋) 을 물려받은 것이다."""
    pane = inherited if inherited else Pane.spawn()
    log(gen, f"pane pid={pane.pid} produced={pane.produced} ring={len(pane.ring)}")

    lsock = socket.socket()
    lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    lsock.bind((HOST, PORT))
    lsock.listen(4)

    hsock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    if os.path.exists(HANDOFF_SOCK):
        os.unlink(HANDOFF_SOCK)
    hsock.bind(HANDOFF_SOCK)
    hsock.listen(1)

    client: socket.socket | None = None
    log(gen, f"listening {HOST}:{PORT}")

    while True:
        watch = [lsock, hsock, pane.fd] + ([client] if client else [])
        r, _, _ = select.select(watch, [], [], 0.05)

        if lsock in r:
            c, _ = lsock.accept()
            line = b""
            while not line.endswith(b"\n"):
                line += c.recv(1)
            _, _pane, frm = line.decode().split()
            frm = int(frm)
            back = pane.since(frm)
            c.sendall(f"OFFSET {pane.produced - len(back)}\n".encode() + back)
            client = c
            log(gen, f"client attached from {frm}, replayed {len(back)}B")

        if pane.fd in r:
            data = pane.read()
            if data and client:
                try:
                    client.sendall(data)
                except OSError:
                    client = None

        if client and client in r:
            if not client.recv(4096):
                log(gen, "client gone")
                client = None

        if hsock in r:
            # ── 교체 요청 ─────────────────────────────────────
            conn, _ = hsock.accept()
            conn.recv(64)
            log(gen, "handoff requested")

            # 클라이언트를 먼저 끊는다. 새 데몬이 뜨는 동안 바이트는 커널 PTY 버퍼에 쌓인다.
            if client:
                client.close()
                client = None
            lsock.close()
            hsock.close()
            os.unlink(HANDOFF_SOCK)

            parent, child_end = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
            os.set_inheritable(child_end.fileno(), True)
            subprocess.Popen(
                [sys.executable, SELF, "serve", str(gen + 1), "--inherit", str(child_end.fileno())],
                pass_fds=(child_end.fileno(),),
            )
            child_end.close()

            payload = json.dumps(pane.state()).encode()
            socket.send_fds(parent, [struct.pack("!I", len(payload)) + payload + bytes(pane.ring)], [pane.fd])
            parent.recv(8)                      # 새 데몬이 받았다고 할 때까지 기다린다
            log(gen, "handed off, exiting")
            os._exit(0)


def serve_inherited(gen: int, fdnum: int) -> None:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM, fileno=fdnum)
    msg, fds, _, _ = socket.recv_fds(sock, 4 * 1024 * 1024, 1)
    n = struct.unpack("!I", msg[:4])[0]
    st = json.loads(msg[4 : 4 + n])
    ring = msg[4 + n :]
    sock.sendall(b"OK")
    pane = Pane(fds[0], st["pid"], produced=st["produced"], ring=ring)
    log(gen, f"inherited fd={fds[0]} produced={st['produced']} ring={len(ring)}B (state said {st['ring']}B)")
    serve(gen, inherited=pane)


def client_run(seconds: float) -> None:
    """붙어서 순번을 센다. 끊기면 마지막 오프셋으로 즉시 다시 붙는다."""
    seen: set[int] = set()
    offset = 0
    downtime: list[float] = []
    buf = b""
    end = time.time() + seconds
    attaches = 0
    last_byte = time.time()

    while time.time() < end:
        try:
            s = socket.create_connection((HOST, PORT), timeout=2)
        except OSError:
            time.sleep(0.01)
            continue
        s.sendall(f"ATTACH 0 {offset}\n".encode())
        head = b""
        while not head.endswith(b"\n"):
            head += s.recv(1)
        offset = int(head.split()[1])
        attaches += 1
        if attaches > 1:
            downtime.append(time.time() - last_byte)

        s.settimeout(0.5)
        while time.time() < end:
            try:
                b = s.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                b = b""
            if not b:
                break
            last_byte = time.time()
            offset += len(b)
            buf += b
            *lines, buf = buf.split(b"\n")
            for l in lines:
                m = re.search(rb"SEQ (\d+)", l)
                if m:
                    seen.add(int(m.group(1)))
        s.close()

    lo, hi = (min(seen), max(seen)) if seen else (0, 0)
    missing = sorted(set(range(lo, hi + 1)) - seen) if seen else []
    print(json.dumps({
        "attaches": attaches,
        "seq_lo": lo, "seq_hi": hi, "seen": len(seen),
        "missing": len(missing), "missing_sample": missing[:10],
        "downtime_ms": [round(d * 1000, 1) for d in downtime],
    }))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("role", choices=["serve", "client", "run"])
    ap.add_argument("gen", nargs="?", default="1")
    ap.add_argument("--inherit", type=int)
    ap.add_argument("--seconds", type=float, default=6.0)
    ap.add_argument("--fast", action="store_true")
    a = ap.parse_args()
    global PANE_CMD
    if a.fast:
        PANE_CMD = FAST

    if a.role == "serve":
        if a.inherit is not None:
            serve_inherited(int(a.gen), a.inherit)
        else:
            serve(int(a.gen))
    elif a.role == "client":
        client_run(a.seconds)
    else:
        extra = ["--fast"] if a.fast else []
        d = subprocess.Popen([sys.executable, SELF, "serve", "1"] + extra)
        time.sleep(0.8)
        c = subprocess.Popen([sys.executable, SELF, "client", "--seconds", "6"], stdout=subprocess.PIPE)
        time.sleep(3.0)
        t = time.time()
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(HANDOFF_SOCK)
        s.sendall(b"GO")
        s.close()
        out, _ = c.communicate(timeout=30)
        print("교체 요청 →", round((time.time() - t) * 1000), "ms 안에 결과 수집 끝")
        print(out.decode().strip())
        for p in (d,):
            p.poll() or p.kill()
        subprocess.run(["pkill", "-f", "SEQ \\$i"], capture_output=True)


if __name__ == "__main__":
    main()
