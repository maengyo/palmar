// 스파이크 D — PTY → 웹소켓 → xterm.js 파이프라인 (Bun 판)
//
// 파이썬 판과 **같은 프로토콜, 같은 상수**를 쓴다. 그래야 같은 페이지로 둘을 재고 비교할 수 있다.
// Bun 은 PTY(`Bun.Terminal`)와 웹소켓 서버(`Bun.serve`)가 내장이라 의존성이 없다.
//
// 주의(조사): 미리 만든 `Bun.Terminal` 을 spawn 에 넘기면 제어 터미널이 안 붙는 버그가 있다.
// 반드시 인라인 `terminal: {...}` 형태로만 쓴다.

import { join } from "path";

const HIGH_WATER = 100_000;
const LOW_WATER = 10_000;
const COALESCE_MS = 5;

const WEB = join(import.meta.dir, "..", "web");
const port = Number(process.argv.find((a) => a.startsWith("--port="))?.slice(7) ?? 8802);

type Pane = {
  term: any;
  proc: any;
  unacked: number;
  paused: boolean;
  pending: Uint8Array[];
  pendingLen: number;
  timer: ReturnType<typeof setTimeout> | null;
  closed: boolean;
};

const panes = new WeakMap<object, Pane>();

function flush(ws: any, p: Pane) {
  p.timer = null;
  if (p.closed || p.pendingLen === 0) return;
  const buf = new Uint8Array(p.pendingLen);
  let o = 0;
  for (const c of p.pending) {
    buf.set(c, o);
    o += c.length;
  }
  p.pending = [];
  p.pendingLen = 0;
  p.unacked += buf.length;
  ws.send(buf);
  if (p.unacked >= HIGH_WATER && !p.paused) {
    p.paused = true; // Bun.Terminal 에 pause 가 없어(이슈 #41410) 표시만 해 둔다.
    // 이 플래그는 아무것도 막지 않는다 — 위의 ws.send 는 그대로 나간다.
    // 보내는 것을 막아도 PTY 는 계속 읽히므로 메모리에 쌓이는 것은 같다(RSS 87MB).
    // 진짜로 멈추려면 자식에게 SIGSTOP 을 보내야 하고, 그건 다른 종류의 값이다.
  }
}

const server = Bun.serve({
  hostname: "127.0.0.1", // 밖으로 열지 않는다
  port,
  async fetch(req, srv) {
    const url = new URL(req.url);
    if (url.pathname === "/pty") {
      if (srv.upgrade(req, { data: { q: url.searchParams } })) return;
      return new Response("upgrade failed", { status: 400 });
    }
    const name = url.pathname === "/" ? "index.html" : url.pathname.slice(1);
    const file = Bun.file(join(WEB, name));
    if (!(await file.exists())) return new Response("not found", { status: 404 });
    return new Response(file, { headers: { "Cache-Control": "no-store" } });
  },
  websocket: {
    open(ws: any) {
      const q: URLSearchParams = ws.data.q;
      const cols = Number(q.get("cols") ?? 80);
      const rows = Number(q.get("rows") ?? 24);
      const cmd = (q.get("cmd") ?? process.env.SHELL ?? "/bin/sh").split(" ");
      const cwd = q.get("cwd") ?? process.env.HOME;

      const p: Pane = {
        term: null, proc: null, unacked: 0, paused: false,
        pending: [], pendingLen: 0, timer: null, closed: false,
      };
      const proc = Bun.spawn(cmd, {
        cwd,
        env: { ...process.env, TERM: "xterm-256color", PALMAR_PANE: "spike" },
        terminal: {
          cols, rows,
          data(_term: any, bytes: Uint8Array) {
            if (p.closed) return;
            p.pending.push(bytes);
            p.pendingLen += bytes.length;
            if (p.timer === null) p.timer = setTimeout(() => flush(ws, p), COALESCE_MS);
          },
          exit() {
            p.closed = true;
            try { ws.close(); } catch {}
          },
        },
      });
      p.proc = proc;
      p.term = (proc as any).terminal;
      panes.set(ws, p);
    },
    message(ws: any, msg: string | Uint8Array) {
      const p = panes.get(ws);
      if (!p || p.closed) return;
      if (typeof msg === "string") {
        const m = JSON.parse(msg);
        if (m.t === "resize") p.term?.resize(m.cols, m.rows);
        else if (m.t === "ack") {
          p.unacked = Math.max(0, p.unacked - m.n);
          if (p.paused && p.unacked <= LOW_WATER) p.paused = false;
        }
      } else {
        p.term?.write(msg);
      }
    },
    close(ws: any) {
      const p = panes.get(ws);
      if (!p) return;
      p.closed = true;
      if (p.timer) clearTimeout(p.timer);
      try { p.term?.close(); } catch {}
      try { p.proc?.kill(); } catch {}
    },
  },
});

console.log(`bun     http://127.0.0.1:${server.port}  pid ${process.pid}`);
