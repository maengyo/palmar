"""What every test needs: a daemon of its own, and a way to talk to it.

**Never the real `~/.palmar`.** Every daemon here gets a temp HOME, so a test cannot touch the
token, the key or the panes of the one you are actually using. That is not politeness — the daemon
rotates the token at start-up and takes an exclusive lock per HOME, so a test that used the real one
would log you out of your own session.

Standard library only, like everything else in this repo, and 3.9 syntax.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON = sys.executable
START_TIMEOUT = 20.0


def free_port() -> int:
    """A port nothing is listening on. Racy in principle; in practice the daemon binds it moments
    later and a collision shows up as a clean start-up failure, not a mystery."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Daemon:
    """A palmar daemon in a home of its own. Use it as a context manager.

        with Daemon() as d:
            d.post("/api/sessions", {"cwd": d.home})
    """

    def __init__(self, env=None, shell=None, browser=False):
        # browser=False adds --no-browser, or every test on this machine opens a browser window.
        # A test that wants to measure the opening itself passes browser=True *and* a $BROWSER that
        # only writes down its argv — never one that really opens something.
        self.browser = browser
        self.home = tempfile.mkdtemp(prefix="palmar-test-")
        self.port = free_port()
        self.proc = None
        self._extra = dict(env or {})
        if shell:
            self._extra["SHELL"] = shell

    # ── lifecycle ────────────────────────────────────────
    def start(self):
        env = dict(os.environ, HOME=self.home)
        env.pop("LC_ALL", None)          # a test should not inherit the running shell's locale
        env.update(self._extra)
        self.proc = subprocess.Popen(
            [PYTHON, "-m", "palmar", "--port", str(self.port)]
            # **--foreground.** The daemon detaches by default now (2026-09-14), and a test that
            # cannot terminate what it started leaks a daemon per test class.
            + ["--foreground"]
            + ([] if self.browser else ["--no-browser"]),
            cwd=REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        end = time.time() + START_TIMEOUT
        while time.time() < end:
            if self.proc.poll() is not None:
                out = (self.proc.stdout.read() or b"") + (self.proc.stderr.read() or b"")
                raise RuntimeError("daemon exited at start-up: " + out.decode("utf-8", "replace")[-400:])
            if os.path.exists(self.url_file):
                try:
                    # The daemon may have moved to the next port when this one was taken — the
                    # address in run/url is the truth, the number picked above only the request.
                    with open(self.url_file, encoding="utf-8") as fh:
                        got = fh.read().strip().split("//", 1)[1].split("/", 1)[0].rpartition(":")[2]
                    if got.isdigit():
                        self.port = int(got)
                    self.get("/api/sessions")
                    return self
                except Exception:
                    pass
            time.sleep(0.1)
        raise RuntimeError("daemon did not come up on port %d" % self.port)

    def stop(self, wipe=True):
        """Stop it the way Ctrl-C does, so the shutdown path runs — that is where the restore file
        is written, and a test that killed it with SIGKILL would quietly skip that."""
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        if self.proc:
            for pipe in (self.proc.stdout, self.proc.stderr):
                try:
                    pipe.close()
                except Exception:
                    pass
        if wipe:
            shutil.rmtree(self.home, ignore_errors=True)

    def restart(self):
        """Stop and start again **on the same home** — how an update looks from the daemon's side,
        and the only way to test anything about surviving one."""
        self.stop(wipe=False)
        self.port = free_port()
        return self.start()

    def __enter__(self):
        return self.start()

    def __exit__(self, *a):
        self.stop()
        return False

    # ── what it wrote about itself ───────────────────────
    @property
    def run(self):
        return os.path.join(self.home, ".palmar", "run")

    @property
    def url_file(self):
        return os.path.join(self.run, "url")

    def _read(self, name):
        with open(os.path.join(self.run, name)) as fh:
            return fh.read().strip()

    @property
    def token(self):
        return self._read("token")

    @property
    def key(self):
        return self._read("key")

    @property
    def url(self):
        return self._read("url")

    @property
    def base(self):
        return "http://127.0.0.1:%d" % self.port

    def log(self):
        """Whatever the daemon has printed to stderr so far, without blocking."""
        try:
            os.set_blocking(self.proc.stderr.fileno(), False)
            return (self.proc.stderr.read() or b"").decode("utf-8", "replace")
        except Exception:
            return ""

    # ── HTTP ─────────────────────────────────────────────
    def raw(self, method, path, body=None, token=True, headers=None):
        """(status, body bytes). A 4xx is a result, not an exception — most of what these tests
        assert is *which* refusal came back."""
        url = self.base + path
        if token:
            url += ("&" if "?" in path else "?") + "token=" + self.token
        # **bytes go as they are.** Everything here speaks JSON except PUT /api/file, whose body is the
        # file (2026-09-15) — so the rule is the type, not another argument.
        data = body if isinstance(body, bytes) else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Origin", self.base)
        if data:
            req.add_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def get(self, path, **kw):
        st, b = self.raw("GET", path, **kw)
        if st != 200:
            raise RuntimeError("GET %s -> %d %s" % (path, st, b[:200]))
        return json.loads(b or b"null")

    def post(self, path, body=None, **kw):
        st, b = self.raw("POST", path, body, **kw)
        if st not in (200, 201):
            raise RuntimeError("POST %s -> %d %s" % (path, st, b[:200]))
        return json.loads(b or b"null")

    def delete(self, path, **kw):
        st, b = self.raw("DELETE", path, **kw)
        if st not in (200, 204):
            raise RuntimeError("DELETE %s -> %d %s" % (path, st, b[:200]))
        return None

    def page(self, with_key=True):
        """The HTML, as a browser would be given it. (status, text)."""
        url = self.base + "/"
        if with_key:
            url += "?k=" + self.key
        req = urllib.request.Request(url)
        req.add_header("Origin", self.base)
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")

    # ── sessions, the way a test wants them ──────────────
    def open_pane(self, cwd=None, name=None, canvas=None):
        body = {"cwd": cwd or self.home}
        if name:
            body["name"] = name
        if canvas:
            body["canvas"] = canvas
        return self.post("/api/sessions", body)

    def panes(self):
        return self.get("/api/sessions")

    def canvases(self):
        return self.get("/api/canvases")

    def wait_status(self, sid, want, seconds=12.0):
        """Wait for one pane to reach a status. Returns what it saw, so a failing assert can say
        what it settled on instead of just 'not equal'."""
        end = time.time() + seconds
        seen = None
        while time.time() < end:
            for s in self.panes():
                if s["id"] == sid:
                    seen = s["status"]
                    if seen == want:
                        return seen
            time.sleep(0.25)
        return seen

    def statuses(self, sid, seconds=8.0, step=0.5):
        """The trail of a pane's status over time — the only way to tell 'it settled' from
        'it never moved'."""
        out = []
        end = time.time() + seconds
        while time.time() < end:
            for s in self.panes():
                if s["id"] == sid:
                    out.append(s["status"])
            time.sleep(step)
        return out


# ── a websocket client, only as much as the tests need ──────────────────────────────────────
class WS:
    """Enough RFC 6455 to connect, read frames and send them masked. The daemon rejects unmasked
    client frames, so masking is not optional here."""

    def __init__(self, d: Daemon, path):
        self.sock = socket.create_connection(("127.0.0.1", d.port), timeout=10)
        self.sock.settimeout(10)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((
            "GET %s HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nOrigin: %s\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n"
            % (path, d.port, d.base, key)).encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("the daemon closed during the handshake")
            buf += chunk
        head, self.buf = buf.split(b"\r\n\r\n", 1)
        self.status = int(head.split(b" ")[1])

    def _need(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise EOFError
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def recv(self):
        """(opcode, payload). Raises EOFError when the daemon hangs up."""
        h = self._need(2)
        op = h[0] & 0x0F
        n = h[1] & 0x7F
        if n == 126:
            n = struct.unpack("!H", self._need(2))[0]
        elif n == 127:
            n = struct.unpack("!Q", self._need(8))[0]
        return op, (self._need(n) if n else b"")

    def recv_json(self):
        while True:
            op, payload = self.recv()
            if op == 0x1:
                return json.loads(payload)

    def send(self, payload, opcode=0x1, mask=True):
        n = len(payload)
        flag = 0x80 if mask else 0
        if n < 126:
            head = struct.pack("!BB", 0x80 | opcode, flag | n)
        elif n < 65536:
            head = struct.pack("!BBH", 0x80 | opcode, flag | 126, n)
        else:
            head = struct.pack("!BBQ", 0x80 | opcode, flag | 127, n)
        if not mask:
            self.sock.sendall(head + payload)
            return
        m = os.urandom(4)
        self.sock.sendall(head + m + bytes(b ^ m[i % 4] for i, b in enumerate(payload)))

    def alive(self, seconds=2.0):
        """Did the daemon keep this connection? A closed socket reads as empty."""
        self.sock.settimeout(seconds)
        try:
            return bool(self.sock.recv(4096))
        except socket.timeout:
            return True
        except OSError:
            return False

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass
