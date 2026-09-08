// 스파이크 D — 브라우저 쪽. 파이썬 서버와 Bun 서버 어느 쪽에 붙어도 같은 코드다.
//
// 재는 것: 프레임 시간(p50/p95/최악), 긴 작업, 키→화면 지연, 받은 바이트.
// **숫자가 좋아도 사람이 봐야 한다** — 미끄러운지, 글자가 물결치는지는 눈으로 본다.

const $ = (id) => document.getElementById(id);
const canvas = $("canvas");

// ── 프레임 계측 ────────────────────────────────────────
const frames = [];          // 최근 프레임 간격(ms)
let longTasks = 0, longMax = 0;
let last = performance.now();

function tick(now) {
  frames.push(now - last);
  last = now;
  if (frames.length > 600) frames.shift();
  requestAnimationFrame(tick);
}
requestAnimationFrame(tick);

if (window.PerformanceObserver) {
  try {
    new PerformanceObserver((list) => {
      for (const e of list.getEntries()) { longTasks++; longMax = Math.max(longMax, e.duration); }
    }).observe({ entryTypes: ["longtask"] });
  } catch {}
}

// ── 입력 지연 계측 ─────────────────────────────────────
// 키를 보낸 시각을 적어 두고, 그 글자가 담긴 바이트가 **브라우저에 도착한** 시각에서 뺀다.
// 주의: 이것은 화면에 그려진 시각이 아니다. term.write() 의 파싱 콜백도, 다음 프레임의
// 페인트도 기다리지 않는다. 그러니 이 값은 "키 → 에코 왕복(그리기 직전까지)" 이다.
// 또 하나: 판정이 부분 문자열 일치라, **계측하는 pane 자신이 출력 중이면 값이 무의미하다**
// (floodall 이 그렇다). 그래서 아래는 pane 0 에서만 재고, flood 는 pane 1 을 때린다.
const latencies = [];
let pendingKey = null;   // {ch, t}

function noteKeySent(ch) { pendingKey = { ch, t: performance.now() }; }
function noteEcho(text) {
  if (pendingKey && text.includes(pendingKey.ch)) {
    latencies.push(performance.now() - pendingKey.t);
    if (latencies.length > 200) latencies.shift();
    pendingKey = null;
  }
}

const pct = (arr, p) => {
  if (!arr.length) return null;
  const s = [...arr].sort((a, b) => a - b);
  return s[Math.min(s.length - 1, Math.floor(p * s.length))];
};
const fmt = (v) => (v == null ? "—" : v.toFixed(1) + "ms");
const cls = (v, warn, bad) => (v == null ? "" : v >= bad ? "b" : v >= warn ? "w" : "g");

// ── pane ──────────────────────────────────────────────
const panes = [];
let totalBytes = 0, bytesWindow = [], glCount = 0;

class PaneWin {
  constructor(i, x, y, w, h) {
    this.i = i;
    this.el = document.createElement("div");
    this.el.className = "win";
    Object.assign(this.el.style, { left: x + "px", top: y + "px", width: w + "px", height: h + "px" });
    this.el.innerHTML =
      `<div class="bar"><b>pane ${i}</b><span class="sz"></span></div>` +
      `<div class="body"></div><div class="grip"></div>`;
    canvas.appendChild(this.el);

    this.term = new Terminal({
      fontSize: 12,
      fontFamily: 'ui-monospace, "SF Mono", Menlo, monospace',
      theme: { background: "#0e1116", foreground: "#d8dae0" },
      scrollback: 1000,
      allowProposedApi: true,
    });
    this.fit = new FitAddon.FitAddon();
    this.term.loadAddon(this.fit);
    this.term.open(this.el.querySelector(".body"));

    if ($("renderer").value === "webgl") {
      try {
        const gl = new WebglAddon.WebglAddon();
        gl.onContextLoss(() => { gl.dispose(); this.gl = null; glCount--; });
        this.term.loadAddon(gl);
        this.gl = gl; glCount++;
      } catch { this.gl = null; }
    }
    this.fit.fit();

    // 명령은 안 보낸다 — 데몬이 $SHELL 을 박아 쓴다(#29). 토큰은 서버가 이 페이지에 심어 준다.
    const q = new URLSearchParams({
      cols: String(this.term.cols), rows: String(this.term.rows),
      token: window.PALMAR_TOKEN || "",
    });
    this.ws = new WebSocket(`ws://${location.host}/pty?${q}`);
    this.ws.binaryType = "arraybuffer";

    this.unacked = 0;
    this.ws.onmessage = (ev) => {
      const bytes = new Uint8Array(ev.data);
      totalBytes += bytes.length;
      bytesWindow.push([performance.now(), bytes.length]);
      this.unacked += bytes.length;
      this.term.write(bytes, () => {
        // 흐름 제어: 실제로 파싱이 끝난 뒤에만 ACK 한다.
        if (this.ws.readyState === 1) this.ws.send(JSON.stringify({ t: "ack", n: bytes.length }));
        this.unacked = 0;
      });
      if (this.i === 0) noteEcho(new TextDecoder().decode(bytes));
    };
    this.term.onData((d) => {
      if (this.ws.readyState === 1) this.ws.send(new TextEncoder().encode(d));
    });

    this.el.addEventListener("mousedown", () => {
      document.querySelectorAll(".win.focus").forEach((e) => e.classList.remove("focus"));
      this.el.classList.add("focus");
    });
    this.dragify();
    this.showSize();
  }

  showSize() { this.el.querySelector(".sz").textContent = `${this.term.cols}×${this.term.rows}`; }

  refit() {
    this.fit.fit();
    if (this.ws.readyState === 1)
      this.ws.send(JSON.stringify({ t: "resize", cols: this.term.cols, rows: this.term.rows }));
    this.showSize();
  }

  dragify() {
    const bar = this.el.querySelector(".bar");
    const grip = this.el.querySelector(".grip");
    let mode = null, sx = 0, sy = 0, ox = 0, oy = 0, ow = 0, oh = 0;

    const down = (m) => (e) => {
      mode = m; sx = e.clientX; sy = e.clientY;
      ox = this.el.offsetLeft; oy = this.el.offsetTop;
      ow = this.el.offsetWidth; oh = this.el.offsetHeight;
      document.body.classList.add("interacting");
      e.preventDefault();
    };
    bar.addEventListener("mousedown", down("move"));
    grip.addEventListener("mousedown", down("size"));

    window.addEventListener("mousemove", (e) => {
      if (!mode) return;
      const dx = e.clientX - sx, dy = e.clientY - sy;
      if (mode === "move") {
        this.el.style.left = Math.max(0, ox + dx) + "px";
        this.el.style.top = Math.max(0, oy + dy) + "px";
      } else {
        this.el.style.width = Math.max(200, ow + dx) + "px";
        this.el.style.height = Math.max(90, oh + dy) + "px";
      }
    });
    window.addEventListener("mouseup", () => {
      if (mode === "size") this.refit();   // 크기 조절을 놓았을 때만 PTY 에 알린다
      mode = null;
      document.body.classList.remove("interacting");
    });
  }

  flood() {
    // 셸 안에서 최대 속도로 줄을 찍는다.
    this.ws.send(new TextEncoder().encode(
      "i=0; while :; do i=$((i+1)); echo \"flood $i 0123456789abcdef 0123456789abcdef\"; done\n"
    ));
  }
  stop() { this.ws.send(new TextEncoder().encode("\x03")); }
  close() { try { this.ws.close(); } catch {} this.el.remove(); }
}

// ── 배치: 격자로 깔되 조금 어긋나게 ────────────────────
function layout(n) {
  const W = canvas.clientWidth, H = canvas.clientHeight;
  const cols = Math.ceil(Math.sqrt(n * (W / H)));
  const rows = Math.ceil(n / cols);
  const gap = 12;
  const w = Math.floor((W - gap * (cols + 1)) / cols);
  const h = Math.floor((H - gap * (rows + 1)) / rows);
  return Array.from({ length: n }, (_, i) => {
    const c = i % cols, r = Math.floor(i / cols);
    return [gap + c * (w + gap), gap + r * (h + gap), w, h];
  });
}

function spawn() {
  reset();
  const n = Number($("panes").value);
  layout(n).forEach(([x, y, w, h], i) => panes.push(new PaneWin(i, x, y, w, h)));
  setTimeout(() => panes.forEach((p) => p.refit()), 120);
}

function reset() {
  panes.forEach((p) => p.close());
  panes.length = 0;
  glCount = 0; totalBytes = 0; bytesWindow = [];
  frames.length = 0; latencies.length = 0; longTasks = 0; longMax = 0;
}

// ── 자동 드래그 왕복 (손 대신 흔들어 프레임을 잰다) ────
let dragTimer = null;
function autoDrag(on) {
  if (!on) { clearInterval(dragTimer); dragTimer = null; return; }
  const p = panes[0];
  if (!p) return;
  let t = 0;
  const x0 = p.el.offsetLeft, y0 = p.el.offsetTop;
  dragTimer = setInterval(() => {
    t += 0.08;
    p.el.style.left = x0 + Math.sin(t) * 160 + "px";
    p.el.style.top = y0 + Math.cos(t * 0.7) * 90 + "px";
  }, 16);
}

// ── 키 지연 측정: pane 0 에 주기적으로 한 글자 ─────────
setInterval(() => {
  const p = panes[0];
  if (!p || p.ws.readyState !== 1 || pendingKey) return;
  const ch = String.fromCharCode(97 + Math.floor(Math.random() * 26));
  noteKeySent(ch);
  p.ws.send(new TextEncoder().encode(ch));
}, 700);

// ── HUD ────────────────────────────────────────────────
setInterval(() => {
  const p50 = pct(frames, 0.5), p95 = pct(frames, 0.95), pmax = frames.length ? Math.max(...frames) : null;
  const fps = p50 ? 1000 / p50 : null;
  $("fps").textContent = fps ? fps.toFixed(0) : "—";
  $("fps").className = fps == null ? "" : fps >= 50 ? "g" : fps >= 30 ? "w" : "b";
  for (const [id, v, warn, bad] of [["p50", p50, 20, 34], ["p95", p95, 34, 60], ["pmax", pmax, 60, 150]]) {
    $(id).textContent = fmt(v); $(id).className = cls(v, warn, bad);
  }
  $("long").textContent = longTasks ? `${longTasks}회 · 최악 ${longMax.toFixed(0)}ms` : "0";
  $("long").className = longMax >= 100 ? "b" : longMax >= 50 ? "w" : "g";

  const l50 = pct(latencies, 0.5), lmax = latencies.length ? Math.max(...latencies) : null;
  $("lat50").textContent = fmt(l50); $("lat50").className = cls(l50, 50, 120);
  $("latmax").textContent = fmt(lmax); $("latmax").className = cls(lmax, 120, 300);

  const now = performance.now();
  bytesWindow = bytesWindow.filter(([t]) => now - t < 1000);
  const bps = bytesWindow.reduce((a, [, n]) => a + n, 0);
  $("npane").textContent = panes.length;
  $("ngl").textContent = glCount;
  $("bytes").textContent = (totalBytes / 1048576).toFixed(1) + " MB";
  $("bps").textContent = (bps / 1024).toFixed(0) + " KB/s";
}, 250);

// ── 버튼 ───────────────────────────────────────────────
$("spawn").onclick = spawn;
$("reset").onclick = reset;
$("flood").onclick = (e) => {
  const on = e.target.classList.toggle("on");
  if (panes[1] || panes[0]) (on ? (panes[1] ?? panes[0]).flood() : (panes[1] ?? panes[0]).stop());
};
$("floodall").onclick = (e) => {
  const on = e.target.classList.toggle("on");
  panes.forEach((p) => (on ? p.flood() : p.stop()));
};
$("drag").onclick = (e) => autoDrag(e.target.classList.toggle("on"));
window.addEventListener("resize", () => panes.forEach((p) => p.refit()));

// ── 자동 시나리오 (스크린샷용) ─────────────────────────
// ?panes=8&auto=drag,flood&warm=6  → 띄우고, 켜고, warm 초 뒤부터 계측을 리셋한다
const P = new URLSearchParams(location.search);
if (P.has("panes")) $("panes").value = P.get("panes");
if (P.has("renderer")) $("renderer").value = P.get("renderer");
const scenario = (P.get("auto") || "").split(",").filter(Boolean);

$("top").insertAdjacentHTML("beforeend",
  '<span id="tag" style="color:var(--accent);font:12px ui-monospace,monospace;margin-left:10px"></span>');

spawn();

if (scenario.length || P.has("panes")) {
  const label = [`pane ${$("panes").value}`, $("renderer").value, ...scenario].join(" · ");
  $("tag").textContent = label + " · 예열 중";
  setTimeout(() => {
    if (scenario.includes("flood")) $("flood").click();
    if (scenario.includes("floodall")) $("floodall").click();
    if (scenario.includes("drag")) $("drag").click();
    // 시나리오를 켠 뒤 예열하고 계측을 비운다 — 켜는 순간의 튐을 빼려는 것이다
    setTimeout(() => {
      frames.length = 0; latencies.length = 0; longTasks = 0; longMax = 0;
      $("tag").textContent = label + " · 계측 중";

      // ?measure=N 이면 N 초 뒤 결과를 서버로 보낸다 (헤드리스에서 쓴다)
      const secs = Number(P.get("measure") || 0);
      if (secs) setTimeout(() => {
        const now = performance.now();
        const bps = bytesWindow.filter(([t]) => now - t < 1000).reduce((a, [, n]) => a + n, 0);
        fetch("/report?token=" + encodeURIComponent(window.PALMAR_TOKEN || ""), { method: "POST", body: JSON.stringify({
          label, panes: panes.length, gl: glCount,
          fps: pct(frames, 0.5) ? 1000 / pct(frames, 0.5) : null,
          f50: pct(frames, 0.5), f95: pct(frames, 0.95),
          fmax: frames.length ? Math.max(...frames) : null,
          frames: frames.length,
          longTasks, longMax,
          lat50: pct(latencies, 0.5), latmax: latencies.length ? Math.max(...latencies) : null,
          latN: latencies.length,
          mb: +(totalBytes / 1048576).toFixed(2), kbs: +(bps / 1024).toFixed(0),
          ua: navigator.userAgent.includes("Headless") ? "headless" : "window",
        })}).then(() => { $("tag").textContent = label + " · 보고 완료"; });
      }, secs * 1000);
    }, Number(P.get("warm") || 5) * 1000);
  }, 1500);
}
