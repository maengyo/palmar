"""F2: 두 브라우저가 같은 터미널을 닫으면 진 쪽에 오류 토스트가 뜨는가 (지적 2).
자연스러운 경쟁을 먼저 해 보고, 안 걸리면 Fetch 로 B 의 DELETE 를 붙잡아 순서를 못 박는다."""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from boot import start_daemon, api, hook
from cdp import Chrome

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8834


def open_confirm(t, sid):
    cb = t.js("(()=>{const it=[...document.querySelectorAll('#list .ses')].find(e=>e.dataset.id===%r);"
              "if(!it) return null; const b=it.querySelector('.cl'); const r=b.getBoundingClientRect();"
              "return {x:r.x+r.width/2,y:r.y+r.height/2};})()" % sid)
    if not cb:
        return None
    t.click(cb["x"], cb["y"]); time.sleep(0.25)
    return t.js("(()=>{const y=document.querySelector('#list .cfm .cbtn.yes'); if(!y) return null;"
                "const r=y.getBoundingClientRect(); return {x:r.x+r.width/2,y:r.y+r.height/2};})()")


def state(t):
    return t.js("({toast:{shown:!!document.querySelector('#toast').classList.contains('on'),"
                "txt:document.querySelector('#toast').textContent},"
                "rows:[...document.querySelectorAll('#list .ses')].length,"
                "count:document.querySelector('#count').textContent})")


d = ch = None
try:
    d, home, tok = start_daemon(PORT)
    print("daemon pid", d.pid, "port", PORT)
    st, cvs = api(PORT, tok, "GET", "/api/canvases"); c1 = cvs[0]["id"]
    ids = []
    for i in range(3):
        st, s = api(PORT, tok, "POST", "/api/sessions", {"cwd": home, "canvas": c1, "name": "s%d" % i})
        ids.append(s["id"])
    target = ids[0]
    ch = Chrome(port=9335)
    A = ch.new_tab("http://127.0.0.1:%d/" % PORT)
    B = ch.new_tab("http://127.0.0.1:%d/" % PORT)
    for t in (A, B):
        t.cmd("Page.enable"); t.cmd("Runtime.enable"); t.cmd("Network.enable")
        t.js("window.__st=[];const of=window.fetch;window.fetch=function(u,i){"
             "const p=of.apply(this,arguments);"
             "if(i&&i.method==='DELETE')p.then(r=>window.__st.push(r.status),e=>window.__st.push('err'));"
             "return p};1")
    time.sleep(2.5)
    ya = open_confirm(A, target); yb = open_confirm(B, target)
    print("F2-a confirm boxes A/B:", ya, yb)
    # 자연 경쟁: 거의 동시에 Close
    A.cmd("Input.dispatchMouseEvent", type="mousePressed", x=ya["x"], y=ya["y"], button="left", clickCount=1, buttons=1)
    B.cmd("Input.dispatchMouseEvent", type="mousePressed", x=yb["x"], y=yb["y"], button="left", clickCount=1, buttons=1)
    A.cmd("Input.dispatchMouseEvent", type="mouseReleased", x=ya["x"], y=ya["y"], button="left", clickCount=1, buttons=0)
    B.cmd("Input.dispatchMouseEvent", type="mouseReleased", x=yb["x"], y=yb["y"], button="left", clickCount=1, buttons=0)
    time.sleep(1.5)
    sa, sb = A.js("window.__st"), B.js("window.__st")
    print("F2-b DELETE statuses A:", sa, " B:", sb)
    print("F2-c A:", json.dumps(state(A)))
    print("F2-d B:", json.dumps(state(B)))
    lost = [x for x in (sa + sb) if x == 404]
    got404 = bool(lost)
    toasted = any(state(t)["toast"]["txt"].startswith("close terminal:") for t in (A, B))
    if not got404:
        print("F2-e natural race did not produce a 404 — pinning the order with Fetch interception")
        target2 = ids[1]
        B.cmd("Fetch.enable", patterns=[{"urlPattern": "*/api/sessions/*", "requestStage": "Request"}])
        yb2 = open_confirm(B, target2)
        B.cmd("Input.dispatchMouseEvent", type="mousePressed", x=yb2["x"], y=yb2["y"], button="left", clickCount=1, buttons=1)
        B.cmd("Input.dispatchMouseEvent", type="mouseReleased", x=yb2["x"], y=yb2["y"], button="left", clickCount=1, buttons=0)
        rid = None
        t0 = time.time()
        while rid is None and time.time() - t0 < 8:
            for m in B.ev(0.3):
                if m.get("method") == "Fetch.requestPaused":
                    rid = m["params"]["requestId"]
                    print("F2-f B's DELETE paused:", m["params"]["request"]["method"],
                          m["params"]["request"]["url"].split("?")[0])
        # 그 사이에 "남" 이 먼저 닫는다
        print("F2-g other closer:", api(PORT, tok, "DELETE", "/api/sessions/" + target2)[0])
        time.sleep(0.8)
        B.cmd("Fetch.continueRequest", requestId=rid)
        time.sleep(1.2)
        print("F2-h B DELETE statuses:", B.js("window.__st"))
        print("F2-i B:", json.dumps(state(B)))
        got404 = 404 in (B.js("window.__st") or [])
        toasted = state(B)["toast"]["txt"].startswith("close terminal:")
    alive = [s["id"] for s in api(PORT, tok, "GET", "/api/sessions")[1]]
    print("F2-j sessions left:", len(alive))
    print("VERDICT finding2:", "REPRODUCED" if (got404 and toasted) else "not reproduced")
finally:
    if ch: ch.kill()
    if d:
        d.terminate()
        try: d.wait(5)
        except Exception: d.kill()
