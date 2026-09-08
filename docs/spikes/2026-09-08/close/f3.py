"""F3: 목록 행 확인 줄의 포커스가 Cancel → Close 로 옮겨 가는가 (지적 3). 타일 쪽과 견준다."""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from boot import start_daemon, api, hook
from cdp import Chrome

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8833
d = ch = None
try:
    d, home, tok = start_daemon(PORT)
    print("daemon pid", d.pid, "port", PORT)
    st, cvs = api(PORT, tok, "GET", "/api/canvases"); c1 = cvs[0]["id"]
    ses = []
    for i in range(2):
        st, s = api(PORT, tok, "POST", "/api/sessions", {"cwd": home, "canvas": c1, "name": "s%d" % i})
        ses.append(s["id"])
    aa, bb = ses
    ch = Chrome(port=9334)
    t = ch.new_tab("http://127.0.0.1:%d/" % PORT)
    t.cmd("Page.enable"); t.cmd("Runtime.enable"); t.cmd("Network.enable")
    t.js("window.__dels=[];const of=window.fetch;window.fetch=function(u,i){"
         "if(i&&i.method==='DELETE')window.__dels.push(String(u));return of.apply(this,arguments)};1")
    time.sleep(2.5)
    # ── 목록 행 ──
    cb = t.js("(()=>{const it=[...document.querySelectorAll('#list .ses')].find(e=>e.dataset.id===%r);"
              "const b=it.querySelector('.cl'); const r=b.getBoundingClientRect();"
              "return {x:r.x+r.width/2,y:r.y+r.height/2};})()" % aa)
    t.click(cb["x"], cb["y"]); time.sleep(0.3)
    print("F3-a list confirm open, focus =", t.js("document.activeElement.className"))
    t.js("document.querySelector('#list .cfm .cbtn:not(.yes)').focus();1")
    print("F3-b after moving to Cancel, focus =", t.js("document.activeElement.className"),
          "text =", t.js("document.activeElement.textContent"))
    hook(PORT, tok, bb, "UserPromptSubmit"); time.sleep(0.7)
    r = t.js("({cls:document.activeElement.className,txt:document.activeElement.textContent,"
             "inCfm:!!(document.activeElement.closest&&document.activeElement.closest('#list .cfm'))})")
    print("F3-c after ONE hook on the OTHER session:", json.dumps(r))
    moved = r["cls"].split()[-1] == "yes" if r["cls"] else False
    # 그 자리에서 Enter — 실제로 부수는가
    t.key("Enter", code="Enter", text="\r", vk=13); time.sleep(0.8)
    dels = t.js("window.__dels")
    alive = [s["id"] for s in api(PORT, tok, "GET", "/api/sessions")[1]]
    print("F3-d Enter → DELETE calls:", dels, " aa still alive?", aa in alive)
    # ── 견주기: 타일 제목줄 ──
    st, s = api(PORT, tok, "POST", "/api/sessions", {"cwd": home, "canvas": c1, "name": "tile"})
    dd = s["id"]; time.sleep(1.2)
    tb = t.js("(()=>{const el=[...document.querySelectorAll('.tile')].find(e=>e.dataset.id===%r);"
              "if(!el) return null; const b=el.querySelector('.tb .cl'); const r=b.getBoundingClientRect();"
              "return {x:r.x+r.width/2,y:r.y+r.height/2};})()" % dd)
    print("F3-e tile close box:", tb)
    if tb:
        t.click(tb["x"], tb["y"]); time.sleep(0.3)
        t.js("document.querySelector('.tb .cfm .cbtn:not(.yes)').focus();1")
        hook(PORT, tok, bb, "Stop"); time.sleep(0.7)
        print("F3-f tile side after a hook:",
              json.dumps(t.js("({cls:document.activeElement.className,txt:document.activeElement.textContent,"
                              "stillOpen:!!document.querySelector('.tb .cfm')})")))
    print("VERDICT finding3:", "REPRODUCED" if moved else "not reproduced")
finally:
    if ch: ch.kill()
    if d:
        d.terminate()
        try: d.wait(5)
        except Exception: d.kill()
