"""최소 CDP 클라이언트 — 표준 라이브러리만. 파이썬 3.9."""
import base64, json, os, socket, struct, subprocess, sys, time, urllib.request

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


class WS:
    def __init__(self, url):
        # ws://host:port/path
        rest = url[len("ws://"):]
        hostport, _, path = rest.partition("/")
        host, _, port = hostport.partition(":")
        self.s = socket.create_connection((host, int(port or 80)), timeout=30)
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall(
            ("GET /%s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
             "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % (path, hostport, key)).encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            c = self.s.recv(4096)
            if not c:
                raise RuntimeError("ws handshake closed")
            buf += c
        assert b" 101 " in buf.split(b"\r\n")[0], buf[:120]
        self.buf = buf.split(b"\r\n\r\n", 1)[1]

    def _recv(self, n):
        while len(self.buf) < n:
            c = self.s.recv(65536)
            if not c:
                raise RuntimeError("ws closed")
            self.buf += c
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, payload, opcode=1):
        if isinstance(payload, str):
            payload = payload.encode()
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

    def recv(self):
        while True:
            b0, b1 = self._recv(2)
            op = b0 & 0x0F
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack("!H", self._recv(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", self._recv(8))[0]
            data = self._recv(n) if n else b""
            if op == 8:
                raise RuntimeError("ws close frame")
            if op == 9:
                self.send(data, 10)
                continue
            if op in (1, 2):
                return data

    def close(self):
        try:
            self.s.close()
        except Exception:
            pass


class Chrome:
    def __init__(self, port=9333, profile=None, headless=True, window="1600,1000"):
        self.port = port
        self.profile = profile or ("/tmp/cdp-prof-%d" % port)
        subprocess.run(["rm", "-rf", self.profile])
        args = [CHROME, "--remote-debugging-port=%d" % port, "--user-data-dir=" + self.profile,
                "--no-first-run", "--no-default-browser-check", "--disable-gpu",
                "--window-size=" + window, "about:blank"]
        if headless:
            args.insert(1, "--headless=new")
        self.p = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(200):
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/json/version" % port, timeout=1).read()
                return
            except Exception:
                time.sleep(0.1)
        raise RuntimeError("chrome did not come up")

    def new_tab(self, url):
        req = urllib.request.Request("http://127.0.0.1:%d/json/new?%s" % (self.port, url),
                                     method="PUT")
        r = urllib.request.urlopen(req, timeout=10)
        return Tab(json.loads(r.read())["webSocketDebuggerUrl"])

    def kill(self):
        self.p.terminate()
        try:
            self.p.wait(5)
        except Exception:
            self.p.kill()


class Tab:
    def __init__(self, wsurl):
        self.ws = WS(wsurl)
        self.n = 0
        self.events = []

    def cmd(self, method, **params):
        self.n += 1
        i = self.n
        self.ws.send(json.dumps({"id": i, "method": method, "params": params}))
        while True:
            m = json.loads(self.ws.recv().decode())
            if m.get("id") == i:
                if "error" in m:
                    raise RuntimeError("%s: %s" % (method, m["error"]))
                return m.get("result", {})
            self.events.append(m)

    def ev(self, sec=0.0):
        """지금까지 쌓인 이벤트를 돌려주고, sec 동안 더 읽는다."""
        end = time.time() + sec
        self.ws.s.settimeout(0.05)
        while time.time() < end:
            try:
                self.events.append(json.loads(self.ws.recv().decode()))
            except (socket.timeout, OSError):
                pass
        self.ws.s.settimeout(30)
        out, self.events = self.events, []
        return out

    def js(self, expr, wait=True):
        r = self.cmd("Runtime.evaluate", expression=expr, returnByValue=True,
                     awaitPromise=wait, userGesture=True)
        if r.get("exceptionDetails"):
            raise RuntimeError(json.dumps(r["exceptionDetails"])[:600])
        return r["result"].get("value")

    def click(self, x, y):
        for t in ("mousePressed", "mouseReleased"):
            self.cmd("Input.dispatchMouseEvent", type=t, x=x, y=y, button="left",
                     clickCount=1, buttons=1 if t == "mousePressed" else 0)
            time.sleep(0.02)

    def key(self, key, code=None, text=None, vk=None):
        p = {"key": key}
        if code:
            p["code"] = code
        if text is not None:
            p["text"] = text
        if vk is not None:
            p["windowsVirtualKeyCode"] = vk
            p["nativeVirtualKeyCode"] = vk
        self.cmd("Input.dispatchKeyEvent", type="keyDown", **p)
        self.cmd("Input.dispatchKeyEvent", type="keyUp", **p)

    def shot(self, path):
        r = self.cmd("Page.captureScreenshot", format="png", captureBeyondViewport=False)
        with open(path, "wb") as f:
            f.write(base64.b64decode(r["data"]))
        return path
