"""A headless Chrome, driven over the DevTools protocol. Standard library only.

The browser half of palmar is half the product, and none of it can be checked by asking the daemon.
This is the smallest client that can open the real page and ask it questions.

**Chrome here runs with --disable-gpu and repaints in software.** That is fine for "is the label
right" and "did the tile move" and useless for anything about compositing — the drag trail in #17
cannot be seen this way, and no test here should pretend otherwise.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import time
import urllib.request

CHROME_PATHS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
)


def chrome_path():
    for p in CHROME_PATHS:
        if os.path.exists(p):
            return p
    return None


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class WS:
    def __init__(self, url):
        rest = url[len("ws://"):]
        hostport, _, path = rest.partition("/")
        host, _, port = hostport.partition(":")
        self.s = socket.create_connection((host, int(port or 80)), timeout=30)
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall(("GET /%s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\n"
                        "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
                        "Sec-WebSocket-Version: 13\r\n\r\n" % (path, hostport, key)).encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.s.recv(4096)
        self.buf = buf.split(b"\r\n\r\n", 1)[1]
        self.next_id = 0
        self.events = []

    def _need(self, n):
        while len(self.buf) < n:
            chunk = self.s.recv(65536)
            if not chunk:
                raise EOFError
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _send(self, text):
        payload = text.encode()
        n = len(payload)
        if n < 126:
            head = struct.pack("!BB", 0x81, 0x80 | n)
        elif n < 65536:
            head = struct.pack("!BBH", 0x81, 0x80 | 126, n)
        else:
            head = struct.pack("!BBQ", 0x81, 0x80 | 127, n)
        m = os.urandom(4)
        self.s.sendall(head + m + bytes(b ^ m[i % 4] for i, b in enumerate(payload)))

    def _recv(self):
        while True:
            h = self._need(2)
            op = h[0] & 0x0F
            n = h[1] & 0x7F
            if n == 126:
                n = struct.unpack("!H", self._need(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", self._need(8))[0]
            payload = self._need(n) if n else b""
            if op == 0x1:
                return payload.decode()
            if op == 0x8:
                raise EOFError("chrome closed the connection")

    def call(self, method, params=None, timeout=30):
        self.next_id += 1
        mid = self.next_id
        self._send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        end = time.time() + timeout
        while time.time() < end:
            self.s.settimeout(max(0.1, end - time.time()))
            msg = json.loads(self._recv())
            if "id" not in msg:
                self.events.append(msg)
                continue
            if msg["id"] == mid:
                if "error" in msg:
                    raise RuntimeError(method + ": " + json.dumps(msg["error"]))
                return msg.get("result", {})
        raise TimeoutError(method)

    def close(self):
        try:
            self.s.close()
        except OSError:
            pass


class Browser:
    """One headless Chrome and one page. Use it as a context manager."""

    def __init__(self, width=1400, height=860, scrollbars=False):
        self.port = free_port()
        self.profile = "/tmp/palmar-test-cdp-%d" % self.port
        self.exe = chrome_path()
        self.proc = None
        self.ws = None
        self.size = (width, height)
        # --hide-scrollbars keeps screenshots clean, and forces every scrollbar to zero width. That
        # makes anything about a scrollbar — overlapping it, being covered by it — impossible to see
        # here. A test about one has to turn it off.
        self.scrollbars = scrollbars

    def start(self):
        shutil.rmtree(self.profile, ignore_errors=True)
        self.proc = subprocess.Popen(
            [self.exe, "--headless=new", "--remote-debugging-port=%d" % self.port,
             "--user-data-dir=" + self.profile, "--no-first-run", "--no-default-browser-check",
             "--disable-gpu",
             *([] if self.scrollbars else ["--hide-scrollbars"]),
             "--window-size=%d,%d" % self.size, "--force-device-scale-factor=1", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        end = time.time() + 40
        while time.time() < end:
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/json/version" % self.port,
                                       timeout=1).read()
                return self
            except Exception:
                time.sleep(0.25)
        raise RuntimeError("chrome did not come up")

    def open(self, url, settle=3.5, script=None):
        """Open a page. `script` runs **before the document does**, which is the only way to change
        something the page reads at load time (navigator.platform, localStorage)."""
        req = urllib.request.Request("http://127.0.0.1:%d/json/new?%s" % (self.port, "about:blank"),
                                     method="PUT")
        t = json.loads(urllib.request.urlopen(req, timeout=20).read())
        self.ws = WS(t["webSocketDebuggerUrl"])
        self.ws.call("Runtime.enable")
        self.ws.call("Log.enable")
        self.ws.call("Page.enable")
        if script:
            self.ws.call("Page.addScriptToEvaluateOnNewDocument", {"source": script})
        self.ws.call("Page.navigate", {"url": url})
        time.sleep(settle)
        return self

    def ev(self, expr, timeout=30):
        r = self.ws.call("Runtime.evaluate",
                         {"expression": expr, "returnByValue": True, "awaitPromise": True},
                         timeout=timeout)
        if r.get("exceptionDetails"):
            raise RuntimeError(json.dumps(r["exceptionDetails"])[:600])
        return r["result"].get("value")

    def errors(self):
        """Console errors and exceptions, minus the ones this environment always produces."""
        out = []
        for m in self.ws.events:
            if m.get("method") == "Runtime.exceptionThrown":
                d = m["params"]["exceptionDetails"]
                out.append("EXCEPTION " + str(d.get("text", ""))[:200])
            elif m.get("method") == "Runtime.consoleAPICalled" and m["params"]["type"] == "error":
                args = " ".join(str(a.get("value", a.get("description", "")))
                                for a in m["params"]["args"])
                out.append("ERROR " + args[:200])
        # WebGL is unavailable under --disable-gpu and the page says so on purpose.
        return [e for e in out if "WebGL" not in e]

    def shot(self, path):
        r = self.ws.call("Page.captureScreenshot", {"format": "png"})
        with open(path, "wb") as fh:
            fh.write(base64.b64decode(r["data"]))

    def stop(self):
        if self.ws:
            self.ws.close()
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        shutil.rmtree(self.profile, ignore_errors=True)

    def __enter__(self):
        return self.start()

    def __exit__(self, *a):
        self.stop()
        return False
