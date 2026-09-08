"""F6: 캔버스를 하나도 모르는 데몬(상태 묶음 되돌이 길)에서 접힌 waiting 묶음이 기다리는 줄을 숨기는가.
같은 스텁에 app.js 만 갈아 끼워 옛 판(HEAD)과 지금 작업 트리를 견준다."""
import json, os, subprocess, sys, time, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import Chrome

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
TMP = os.environ.get("TMPDIR", "/tmp")
STUB = os.path.join(TMP, "palmar-stub0.py")


def make_stub():
    """`dev/dev-stub.py` 를 임시로 베껴 **캔버스를 하나도 안 보내게** 고친다.
    제품 데몬으로는 이 길(renderByStatus)에 닿을 수 없기 때문이다 — 계약상 캔버스는 0개가 못 된다.
    저장소의 스텁은 건드리지 않는다. `PALMAR_APPJS` 로 app.js 를 갈아 끼울 수 있게도 해 둔다."""
    s = open(os.path.join(REPO, "dev/dev-stub.py")).read()
    s = s.replace('WEB = Path(__file__).resolve().parent',
                  'WEB = Path(%r) / "web"\nimport os as _os\nAPPJS = _os.environ.get("PALMAR_APPJS")' % REPO)
    s = s.replace('"canvases": [c.json() for c in CANVASES],', '"canvases": [],')
    s = s.replace('respond(writer, 200, jbody([c.json() for c in CANVASES]))', 'respond(writer, 200, jbody([]))')
    s = s.replace('        f = (WEB / name).resolve()',
                  '        if APPJS and name == "app.js":\n'
                  '            respond(writer, 200, open(APPJS, "rb").read(), "application/javascript; charset=utf-8")\n'
                  '            return\n'
                  '        f = (WEB / name).resolve()')
    open(STUB, "w").write(s)
    return STUB
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8836
APPJS = sys.argv[2]


def run():
    env = dict(os.environ, PALMAR_APPJS=APPJS)
    make_stub()
    log = open(os.path.join(TMP, "stub%d.log" % PORT), "wb")
    p = subprocess.Popen(["/usr/bin/python3", STUB, "--port", str(PORT)],
                         env=env, stdout=log, stderr=subprocess.STDOUT)
    for _ in range(200):
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/api/canvases" % PORT, timeout=1).read()
            break
        except Exception:
            time.sleep(0.1)
    else:
        raise RuntimeError("stub did not come up: " + open(os.path.join(TMP, "stub%d.log" % PORT)).read()[-1500:])
    return p


ch = p = None
try:
    p = run()
    print("stub pid", p.pid, "app.js =", os.path.basename(APPJS))
    print("GET /api/canvases →", urllib.request.urlopen("http://127.0.0.1:%d/api/canvases" % PORT).read().decode())
    ch = Chrome(port=9337)
    t = ch.new_tab("http://127.0.0.1:%d/" % PORT)
    t.cmd("Page.enable"); t.cmd("Runtime.enable")
    time.sleep(2.5)
    print("F6-a canvasOrder empty? tabs hidden:",
          t.js("!!document.querySelector('#tabs') && getComputedStyle(document.querySelector('#tabs').parentElement||document.body).display"),
          " groups:", t.js("[...document.querySelectorAll('#list .grp')].map(g=>({key:g.dataset.key,cv:g.dataset.canvas,txt:g.textContent}))"))
    sids = t.js("[...document.querySelectorAll('#list .ses')].map(e=>e.dataset.id)")
    print("F6-b rows:", sids)
    # 하나를 waiting 으로
    tok = t.js("window.PALMAR_TOKEN")
    urllib.request.urlopen(urllib.request.Request(
        "http://127.0.0.1:%d/hook/claude?pane=%s&token=%s" % (PORT, sids[0], tok),
        data=json.dumps({"hook_event_name": "PermissionRequest"}).encode(),
        headers={"content-type": "application/json"}, method="POST")).read()
    time.sleep(0.8)
    g = t.js("(()=>{const g=[...document.querySelectorAll('#list .grp')].find(g=>g.dataset.key==='waiting');"
             "if(!g) return null; const r=g.getBoundingClientRect();"
             "return {car:g.querySelector('.car').textContent, title:g.title, cls:g.className,"
             "cursor:getComputedStyle(g).cursor, x:r.x+r.width/2, y:r.y+r.height/2};})()")
    print("F6-c waiting group header:", json.dumps(g))
    def waiting_rows():
        return t.js("[...document.querySelectorAll('#list .ses')].filter(e=>e.classList.contains('wait')).length")
    print("F6-d waiting rows before click:", waiting_rows())
    t.click(g["x"], g["y"]); time.sleep(0.5)
    after = waiting_rows()
    print("F6-e after clicking the WAITING header: waiting rows =", after,
          " header now:", json.dumps(t.js("(()=>{const g=[...document.querySelectorAll('#list .grp')]"
                                          ".find(g=>g.dataset.key==='waiting'); return g&&{car:g.querySelector('.car').textContent,cls:g.className};})()")))
    print("VERDICT: waiting", "HIDDEN (guarantee broken)" if after == 0 else "still visible (guarantee kept)")
finally:
    if ch: ch.kill()
    if p:
        p.terminate()
        try: p.wait(5)
        except Exception: p.kill()
